"""scanner.py — el orquestador: fuente → (enriquecer) → candidatos → precio → peso → utilidad →
umbrales → persistir + abrir posición de papel. Todo lo demás en el repo es un engranaje que
este módulo ensambla; aquí es donde vive la política de "¿esto es una oportunidad o no?".

Cada paso está diseñado para degradar con gracia en vez de tronar la corrida completa: un host
bloqueado al enriquecer solo pierde el enriquecimiento de ESA oferta, una oferta sin candidatos
simplemente no genera oportunidad. Un error real (bug, API rota) sí debe propagarse — por eso
solo se atrapan las excepciones que la propia arquitectura define como "esperadas".
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from escaner import store
from escaner.candidates import CandidateMatch, CandidateResult, find_candidates
from escaner.config import Settings
from escaner.economics.fees import FeeModel, ShippingModel, TableFeeModel, TableShippingModel
from escaner.economics.profit import CostAssumptions, compute_profit
from escaner.http import BlacklistedHost, PoliteClient, RobotsDisallowed, SourceBlocked
from escaner.matching.normalize import features
from escaner.meli.client import MLClient
from escaner.models import Offer, Opportunity
from escaner.paper import ledger
from escaner.pricing import realistic_sale_price, resolve_weight_g
from escaner.sources.base import Source
from escaner.sources.jsonld import enrich_offer

DEDUPE_WINDOW_DAYS = 3.0


def _escalate_to_judge(
    offer: Offer, cr: CandidateResult, llm_client: Any, settings: Settings, judge_cache_conn: sqlite3.Connection | None
) -> CandidateMatch | None:
    """Última chance para la banda "review" (0.60-0.90): le pregunta al juez LLM SOLO sobre el
    candidato con mayor probabilidad. Nunca se llama si hay veto (`find_candidates` ya los
    excluyó de `cr.review`) ni si `settings.llm_enabled` es False (ver `scan()`)."""
    from escaner.judge import judge_pair

    top = cr.review[0]
    offer_feat = features(offer.title, brand=offer.brand, gtin=offer.gtin, model=offer.model)
    cand_feat = features(
        top.listing.title,
        brand=top.listing.brand,
        gtin=top.listing.gtin,
        model=top.listing.model,
        condition=top.listing.condition,
    )
    result = judge_pair(
        llm_client,
        offer_feat,
        cand_feat,
        top.score,
        model=settings.llm_model,
        effort=settings.llm_effort,
        cache_conn=judge_cache_conn,
    )
    if result.verdict.decision != "match":
        return None
    return CandidateMatch(top.listing, result.verdict)


@dataclass(frozen=True)
class ScanSummary:
    run_id: int
    source: str
    n_offers: int
    n_deduped: int
    n_matched: int
    n_review: int
    n_no_candidate: int
    n_opportunities: int
    opportunities: tuple[Opportunity, ...]


def _maybe_enrich(offer: Offer, polite_client: PoliteClient | None, allowed_hosts: frozenset[str]) -> Offer:
    if polite_client is None or not allowed_hosts:
        return offer
    try:
        return enrich_offer(offer, polite_client, allowed_hosts)
    except (SourceBlocked, RobotsDisallowed, BlacklistedHost):
        return offer  # el enriquecimiento es oportunista: si el host no coopera, se sigue sin él


def _build_fee_shipping_models(ml_client: MLClient) -> tuple[FeeModel, ShippingModel]:
    if ml_client.access_token:
        from escaner.meli.client import ApiFeeModel, ApiShippingModel

        return ApiFeeModel(ml_client), ApiShippingModel(ml_client, user_id=None)
    return TableFeeModel(), TableShippingModel()


def evaluate_offer(
    offer: Offer,
    ml_client: MLClient,
    settings: Settings,
    fee_model: FeeModel,
    shipping_model: ShippingModel,
    llm_client: Any = None,
    judge_cache_conn: sqlite3.Connection | None = None,
) -> tuple[Opportunity | None, str]:
    """Empareja, cotiza y costea UNA oferta. Devuelve `(oportunidad_o_None, motivo)`; `motivo`
    es "match" / "review" / "no_candidate" / "rechazada_por_umbral" para que `scan()` pueda
    llevar el resumen sin repetir esta lógica."""
    cr = find_candidates(offer, ml_client, settings.match_threshold, settings.review_threshold)
    best = cr.matches[0] if cr.matches else None
    judged = False
    if best is None and cr.review and settings.llm_enabled and llm_client is not None:
        best = _escalate_to_judge(offer, cr, llm_client, settings, judge_cache_conn)
        judged = best is not None
    if best is None:
        return None, "review" if cr.review else "no_candidate"

    weight_g, weight_source = resolve_weight_g(offer, best.listing, settings.default_weight_g)
    comparables = [m.listing for m in cr.matches if m.listing.condition == "new"]
    if not comparables and judged:
        # El juez promovió un candidato que no estaba en `cr.matches` (por eso se le preguntó):
        # sin esto, `comparables` quedaría vacío y `realistic_sale_price` no tendría con qué
        # fijar un precio, descartando silenciosamente cada oportunidad que el juez rescató.
        comparables = [best.listing]
    pricing = realistic_sale_price(comparables, cr.buy_box_price, settings.undercut, settings.min_comparables)
    if pricing.sale_price <= 0:
        return None, "sin_precio"

    assumptions = CostAssumptions(
        listing_type=settings.listing_type,
        reputation=settings.reputation,
        tax=settings.tax,
    )
    profit = compute_profit(
        pricing.sale_price,
        offer.price,
        category_id=best.listing.category_id,
        weight_g=weight_g,
        assumptions=assumptions,
        fee_model=fee_model,
        shipping_model=shipping_model,
    )
    flags = []
    if pricing.liquidez_baja:
        flags.append("liquidez_baja")
    if judged:
        flags.append("juzgado_por_llm")

    opportunity = Opportunity(
        offer=offer,
        listing=best.listing,
        match=best.score,
        comparables=tuple(comparables),
        sale_price=pricing.sale_price,
        profit=profit,
        flags=tuple(flags),
        detected_at=datetime.now(UTC),
        weight_source=weight_source,
        buy_box_price=cr.buy_box_price,
    )
    if profit.net_profit < settings.min_profit or profit.roi < settings.min_roi:
        return None, "rechazada_por_umbral"
    return opportunity, "match"


def scan(
    offers_source: Source,
    ml_client: MLClient,
    conn: sqlite3.Connection,
    settings: Settings,
    polite_client: PoliteClient | None = None,
    llm_client: Any = None,
    limit: int = 50,
    open_paper_positions: bool = True,
) -> ScanSummary:
    run_id = store.start_run(conn, offers_source.name)
    raw_offers = offers_source.fetch(limit)
    fee_model, shipping_model = _build_fee_shipping_models(ml_client)

    n_deduped = n_matched = n_review = n_no_candidate = 0
    opportunities: list[Opportunity] = []

    for offer in raw_offers:
        if store.offer_seen_within(conn, offer.source, offer.guid, DEDUPE_WINDOW_DAYS):
            n_deduped += 1
            continue
        offer = _maybe_enrich(offer, polite_client, settings.allowed_product_hosts)
        offer_id = store.save_offer(conn, offer)

        opportunity, reason = evaluate_offer(
            offer, ml_client, settings, fee_model, shipping_model, llm_client=llm_client, judge_cache_conn=conn
        )
        if reason == "review":
            n_review += 1
        elif reason == "no_candidate":
            n_no_candidate += 1
        if opportunity is None:
            continue
        n_matched += 1
        opportunity_id = store.save_opportunity(conn, offer_id, opportunity)
        if open_paper_positions:
            ledger.open_position(conn, opportunity_id, opportunity)
        opportunities.append(opportunity)

    store.finish_run(conn, run_id, len(raw_offers), len(opportunities))
    return ScanSummary(
        run_id=run_id,
        source=offers_source.name,
        n_offers=len(raw_offers),
        n_deduped=n_deduped,
        n_matched=n_matched,
        n_review=n_review,
        n_no_candidate=n_no_candidate,
        n_opportunities=len(opportunities),
        opportunities=tuple(opportunities),
    )
