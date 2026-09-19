"""models.py — los objetos que cruzan todo el escáner: una oferta de un retailer, una
publicación de Mercado Libre, y la oportunidad que resulta de emparejarlas y costear la reventa.

Son dataclasses congeladas (no pydantic): viajan a sqlite y se comparan en tests sin la fricción
de validación de un modelo de frontera. Pydantic se reserva para lo que sí cruza una frontera no
confiable (config desde variables de entorno, salida estructurada del LLM en `judge.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from escaner.economics.profit import ProfitBreakdown
from escaner.matching.score import MatchScore


@dataclass(frozen=True)
class Offer:
    """Una oferta de liquidación vista en una fuente (Promodescuentos, Keepa, CSV, fixtures)."""

    source: str
    merchant: str
    title: str
    price: float
    url: str
    guid: str
    published_at: datetime | None = None
    gtin: str | None = None
    brand: str | None = None
    model: str | None = None
    weight_g: int | None = None
    category: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def dedupe_key(self) -> tuple[str, str]:
        return (self.source, self.guid or self.url)


@dataclass(frozen=True)
class MLListing:
    """Una publicación de Mercado Libre (resultado de `search`, `items` o `product_items`)."""

    item_id: str
    title: str
    price: float
    condition: str = "new"
    free_shipping: bool = False
    listing_type: str = "gold_special"
    category_id: str | None = None
    catalog_product_id: str | None = None
    brand: str | None = None
    gtin: str | None = None
    model: str | None = None
    sold_quantity: int | None = None
    permalink: str | None = None
    seller_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Opportunity:
    """Una `Offer` emparejada con la mejor `MLListing`, costeada de punta a punta.

    `comparables` son las publicaciones nuevas usadas para fijar `sale_price` (ver
    `escaner.pricing`); se guardan para poder re-cotizarlas en `paper.ledger.mark_all` sin
    tener que repetir la búsqueda de candidatos (que podría derivar a otros productos con el
    tiempo).
    """

    offer: Offer
    listing: MLListing
    match: MatchScore
    comparables: tuple[MLListing, ...]
    sale_price: float
    profit: ProfitBreakdown
    flags: tuple[str, ...]
    detected_at: datetime
    weight_source: str = "default"  # "jsonld" | "atributos" | "default"
    buy_box_price: float | None = None  # se guarda para que `paper.ledger.mark_all` re-cotice
    # exactamente con la misma fórmula (si no, remarcar sin buy_box sesgaría la utilidad
    # marcada hacia arriba cada vez que el buy box era la restricción vinculante al detectar).

    @property
    def opportunity_key(self) -> tuple[str, str, str]:
        return (*self.offer.dedupe_key, self.listing.item_id)
