"""ledger.py — abre posiciones de papel desde oportunidades detectadas y las marca a mercado.

Este es el motor del giro "úsalo tú primero" (ESPECIFICACION §1): `mark_all()` re-cotiza cada
posición abierta contra el precio competitivo ACTUAL de Mercado Libre (no el de cuando se
detectó) y contra la vida observable de la oferta de origen. Lo que se puede observar depende
de la fuente:

  - **Keepa**: reconsulta el ASIN; sin oferta (`current_price` da `None`) = la oferta murió.
  - **JSON-LD** (cualquier oferta cuya URL esté en `allowed_product_hosts`): re-fetch cortés,
    máximo 1 vez/día — no tiene sentido pagar el costo (y el riesgo de bloqueo) de releer la
    misma página varias veces al día. Si el host bloquea mientras tanto, la posición se marca
    con la última vida conocida y sigue (no se fuerza un fallo de toda la corrida por un host).
  - **Todo lo demás** (Promodescuentos: su `<link>` casi nunca es la página del retailer — ver
    `sources/promodescuentos.py` — y CSV/fixtures sin URL observable): la vida NO se observa.
    Queda CENSURADA: horas desde publicación, `deal_ended=False`, siempre. Fingir que se observó
    cuando no es cierto sesgaría el Kaplan–Meier hacia vidas más cortas de lo real.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx

from escaner import store
from escaner.config import Settings
from escaner.economics.profit import CostAssumptions, compute_profit
from escaner.http import BlacklistedHost, PoliteClient, RobotsDisallowed, SourceBlocked
from escaner.meli.client import MLApiError, MLClient
from escaner.models import Offer, Opportunity
from escaner.paper.stats import PaperPosition, PaperSummary, summarize
from escaner.pricing import realistic_sale_price, resolve_weight_g
from escaner.sources.jsonld import extract_product_jsonld, host_allowed

POSITION_HORIZON_DAYS = 30.0
JSONLD_RECHECK_HOURS = 24.0


def open_position(conn: sqlite3.Connection, opportunity_id: int, opportunity: Opportunity) -> str:
    position_id = str(uuid.uuid4())
    position = PaperPosition(
        position_id=position_id,
        detection_day=opportunity.detected_at.date().isoformat(),
        capital=opportunity.profit.capital_invested,
        expected_profit=opportunity.profit.net_profit,
        marked_profit=None,
        deal_hours=0.0,
        deal_ended=False,
    )
    store.open_paper_position(conn, opportunity_id, position, opened_at=opportunity.detected_at.isoformat())
    return position_id


# ── observar si la oferta de origen sigue viva ──────────────────────────────────────────


@dataclass(frozen=True)
class LivenessResult:
    alive: bool


class LivenessChecker(Protocol):
    def check(self, offer: Offer) -> LivenessResult | None:
        """`None` = no se pudo (o no se puede) observar esta vez: el llamador debe mantener el
        último estado conocido, no asumir que murió."""
        ...


class KeepaLivenessChecker:
    def __init__(self, keepa_source) -> None:
        self._keepa = keepa_source

    def check(self, offer: Offer) -> LivenessResult | None:
        try:
            price = self._keepa.current_price(offer.guid)
        except Exception:
            return None
        return LivenessResult(alive=price is not None)


class JsonLdLivenessChecker:
    def __init__(self, polite_client: PoliteClient, allowed_hosts: frozenset[str]) -> None:
        self._client = polite_client
        self._hosts = allowed_hosts
        self._last_check: dict[str, datetime] = {}

    def check(self, offer: Offer) -> LivenessResult | None:
        if not host_allowed(offer.url, self._hosts):
            return None
        now = datetime.now(UTC)
        last = self._last_check.get(offer.url)
        if last is not None and (now - last).total_seconds() < JSONLD_RECHECK_HOURS * 3600:
            return None
        self._last_check[offer.url] = now
        try:
            result = self._client.get(offer.url)
        except (SourceBlocked, RobotsDisallowed, BlacklistedHost):
            return None
        product = extract_product_jsonld(result.text)
        if not product:
            return None
        offers_node = product.get("offers") or {}
        if isinstance(offers_node, list):
            offers_node = offers_node[0] if offers_node else {}
        availability = str(offers_node.get("availability") or "").lower()
        alive = "outofstock" not in availability and "discontinued" not in availability
        return LivenessResult(alive=alive)


def _refresh_buy_box(ml_client: MLClient, opportunity: Opportunity) -> float | None:
    """Buy box FRESCO del catálogo (no el snapshot de cuando se detectó): si no se puede (API
    caída, producto dado de baja), se degrada al último buy box conocido — remarcar no debe
    tronar la corrida por un problema de red."""
    try:
        detail = ml_client.product(opportunity.listing.catalog_product_id)
    except (MLApiError, httpx.HTTPError):
        return opportunity.buy_box_price
    bb = (detail.get("buy_box_winner") or {}).get("price")
    return float(bb) if bb is not None else opportunity.buy_box_price


def _observe(
    offer: Offer, keepa_checker: KeepaLivenessChecker | None, jsonld_checker: JsonLdLivenessChecker | None
) -> LivenessResult | None:
    if offer.source == "keepa" and keepa_checker is not None:
        return keepa_checker.check(offer)
    if jsonld_checker is not None:
        return jsonld_checker.check(offer)
    return None


# ── marcar a mercado ─────────────────────────────────────────────────────────────────────


def mark_all(
    conn: sqlite3.Connection,
    ml_client: MLClient,
    settings: Settings,
    keepa_source=None,
    polite_client: PoliteClient | None = None,
    now: datetime | None = None,
) -> int:
    """Re-cotiza cada posición abierta contra ML y contra la vida observable de su oferta.
    Devuelve cuántas posiciones se marcaron (cierra las que ya cumplieron el horizonte)."""
    now = now or datetime.now(UTC)
    keepa_checker = KeepaLivenessChecker(keepa_source) if keepa_source is not None else None
    jsonld_checker = (
        JsonLdLivenessChecker(polite_client, settings.allowed_product_hosts) if polite_client is not None else None
    )
    assumptions = CostAssumptions(listing_type=settings.listing_type, reputation=settings.reputation, tax=settings.tax)

    n = 0
    for row in store.open_positions(conn):
        opp = store.load_opportunity(conn, row["opportunity_id"])
        offer = opp.offer
        opened_at = datetime.fromisoformat(row["opened_at"])
        published = offer.published_at or opened_at

        liveness = _observe(offer, keepa_checker, jsonld_checker)
        deal_hours = (now - published).total_seconds() / 3600.0
        deal_ended = False if liveness is None else not liveness.alive

        ids = [c.item_id for c in opp.comparables]
        refreshed = ml_client.items(ids) if ids else list(opp.comparables)
        buy_box_price = _refresh_buy_box(ml_client, opp) if opp.listing.catalog_product_id else opp.buy_box_price
        pricing = realistic_sale_price(
            refreshed, buy_box_price, undercut=settings.undercut, min_comparables=settings.min_comparables
        )

        marked_price = pricing.sale_price if pricing.sale_price > 0 else None
        marked_profit = None
        if marked_price is not None:
            weight_g, _source = resolve_weight_g(offer, opp.listing, settings.default_weight_g)
            profit = compute_profit(
                marked_price,
                offer.price,
                category_id=opp.listing.category_id,
                weight_g=weight_g,
                assumptions=assumptions,
            )
            marked_profit = profit.net_profit

        days_marked = (now - opened_at).total_seconds() / 86400.0
        store.save_paper_mark(
            conn, row["position_id"], marked_price, marked_profit, deal_hours, deal_ended, days_marked
        )
        n += 1
        if days_marked >= POSITION_HORIZON_DAYS:
            store.close_position(conn, row["position_id"])
    return n


def paper_summary(conn: sqlite3.Connection, min_positions: int = 30, min_days: int = 10) -> PaperSummary:
    return summarize(store.positions_for_stats(conn), min_positions=min_positions, min_days=min_days)
