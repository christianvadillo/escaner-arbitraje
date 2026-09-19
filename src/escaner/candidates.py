"""candidates.py — candidatos de Mercado Libre para una `Offer` de retailer.

Dos caminos (ESPECIFICACION §2, atributos GTIN/BRAND/MODEL del catálogo de ML), en este orden:

  1. **Con GTIN**: `products_search(product_identifier=gtin)` → producto(s) de catálogo →
     buy box (`product()`) + precios de cada publicación (`product_items()`). Es la vía de más
     confianza — mismo GTIN ya lo resuelve `score_pair` a 0.99.
  2. **Sin GTIN**: arma una query de texto (marca + modelo si los hay; si no, tokens
     significativos del título sin colores/stopwords), busca `search` (nuevo, ~50 resultados),
     trae el detalle completo con multiget de `items` (el resultado de `search` suele venir sin
     atributos) y puntúa cada candidato con `score_pair`, incluyendo `price_ratio` = precio en
     ML / precio en el retailer.
"""

from __future__ import annotations

from dataclasses import dataclass

from escaner.matching.normalize import ProductFeatures, features
from escaner.matching.score import MatchScore, score_pair
from escaner.meli.client import MLClient
from escaner.models import MLListing, Offer

# Colores y relleno frecuentes en títulos de retail MX: no ayudan a ubicar el producto y solo
# diluyen la query de texto.
_STOPWORDS = frozenset(
    {
        "de",
        "del",
        "la",
        "el",
        "los",
        "las",
        "para",
        "con",
        "sin",
        "en",
        "un",
        "una",
        "y",
        "o",
        "nuevo",
        "nueva",
        "negro",
        "negra",
        "blanco",
        "blanca",
        "azul",
        "rojo",
        "roja",
        "verde",
        "gris",
        "plata",
        "dorado",
        "dorada",
        "rosa",
        "morado",
        "lila",
        "oscuro",
        "oscura",
    }
)
_MAX_QUERY_TOKENS = 6


@dataclass(frozen=True)
class CandidateMatch:
    listing: MLListing
    score: MatchScore


@dataclass(frozen=True)
class CandidateResult:
    matches: tuple[CandidateMatch, ...]  # decisión == "match", ordenados por probabilidad desc.
    review: tuple[CandidateMatch, ...]  # decisión == "review"
    query: str
    method: str  # "gtin" | "texto"
    buy_box_price: float | None = None


def build_query(offer: Offer) -> str:
    parts = [p for p in (offer.brand, offer.model) if p]
    if not parts:
        norm = features(offer.title).norm
        parts = [t for t in norm.split() if t not in _STOPWORDS and len(t) > 1][:_MAX_QUERY_TOKENS]
    return " ".join(parts) or offer.title


def _score_all(
    offer: Offer,
    offer_feat: ProductFeatures,
    listings: list[MLListing],
    match_threshold: float,
    review_threshold: float,
) -> tuple[list[CandidateMatch], list[CandidateMatch]]:
    matches: list[CandidateMatch] = []
    review: list[CandidateMatch] = []
    seen: set[str] = set()
    for listing in listings:
        if listing.item_id in seen:
            continue
        seen.add(listing.item_id)
        cand_feat = features(
            listing.title, brand=listing.brand, gtin=listing.gtin, model=listing.model, condition=listing.condition
        )
        price_ratio = (listing.price / offer.price) if offer.price else None
        s = score_pair(
            offer_feat, cand_feat, price_ratio, match_threshold=match_threshold, review_threshold=review_threshold
        )
        cm = CandidateMatch(listing, s)
        if s.decision == "match":
            matches.append(cm)
        elif s.decision == "review":
            review.append(cm)
    matches.sort(key=lambda c: -c.score.prob)
    review.sort(key=lambda c: -c.score.prob)
    return matches, review


def _find_by_gtin(
    offer: Offer, offer_feat: ProductFeatures, client: MLClient, match_threshold: float, review_threshold: float
) -> CandidateResult:
    products = client.products_search(product_identifier=offer.gtin)
    listings: list[MLListing] = []
    buy_box_price: float | None = None
    for p in products:
        pid = p.get("id")
        if not pid:
            continue
        listings.extend(client.product_items(pid))
        detail = p if "buy_box_winner" in p else client.product(pid)
        bb = (detail.get("buy_box_winner") or {}).get("price")
        if bb is not None and (buy_box_price is None or float(bb) < buy_box_price):
            buy_box_price = float(bb)
    matches, review = _score_all(offer, offer_feat, listings, match_threshold, review_threshold)
    return CandidateResult(tuple(matches), tuple(review), offer.gtin or "", "gtin", buy_box_price)


def _find_by_text(
    offer: Offer,
    offer_feat: ProductFeatures,
    client: MLClient,
    match_threshold: float,
    review_threshold: float,
    search_limit: int,
) -> CandidateResult:
    query = build_query(offer)
    found = client.search(query, limit=search_limit, condition="new")
    ids = [listing.item_id for listing in found]
    listings = client.items(ids) if ids else []
    matches, review = _score_all(offer, offer_feat, listings, match_threshold, review_threshold)
    return CandidateResult(tuple(matches), tuple(review), query, "texto")


def find_candidates(
    offer: Offer,
    client: MLClient,
    match_threshold: float = 0.90,
    review_threshold: float = 0.60,
    search_limit: int = 50,
) -> CandidateResult:
    offer_feat = features(offer.title, brand=offer.brand, gtin=offer.gtin, model=offer.model)
    if offer.gtin:
        return _find_by_gtin(offer, offer_feat, client, match_threshold, review_threshold)
    return _find_by_text(offer, offer_feat, client, match_threshold, review_threshold, search_limit)
