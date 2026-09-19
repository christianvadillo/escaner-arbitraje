"""pricing.py — precio de venta realista (ESPECIFICACION §5).

No el más caro de Mercado Libre — ese es el que un vendedor optimista pondría, no al que de
verdad se vende rápido:

    precio_venta = min(buy_box, p25 de publicaciones NUEVAS comparables con envío gratis)
                   × (1 − undercut)

`undercut` (2 % por defecto) es el margen de "vender un poco más barato que el más barato
razonable" para no quedarse el último de la fila. Si hay menos de `min_comparables`
publicaciones utilizables, la oportunidad se marca `liquidez_baja`: el precio sigue siendo la
mejor estimación disponible, pero con menos confianza (pocos datos → un solo vendedor
excéntrico puede estar fijando el p25).
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from escaner.models import MLListing, Offer

_WEIGHT_ATTR_RE = re.compile(r"([\d.]+)\s*(kg|g)\b")


@dataclass(frozen=True)
class PricingResult:
    sale_price: float
    buy_box_price: float | None
    p25_price: float | None
    n_comparables: int
    liquidez_baja: bool
    demand_signal: float | None
    notes: tuple[str, ...] = ()


def _p25(prices: list[float]) -> float | None:
    if not prices:
        return None
    if len(prices) == 1:
        return prices[0]
    return statistics.quantiles(sorted(prices), n=4, method="inclusive")[0]


def demand_signal_from(listings: Sequence[MLListing]) -> float | None:
    """Señal de demanda DÉBIL: `sold_quantity` es "referencial" desde abr-2025 (ESPECIFICACION
    §2). Se promedia lo que haya, sin pretender más precisión de la que tiene."""
    vals = [float(listing.sold_quantity) for listing in listings if listing.sold_quantity is not None]
    return statistics.fmean(vals) if vals else None


def realistic_sale_price(
    comparables: Sequence[MLListing],
    buy_box_price: float | None = None,
    undercut: float = 0.02,
    min_comparables: int = 3,
) -> PricingResult:
    """`comparables` debería ser el conjunto de publicaciones que igualaron el producto (no hace
    falta pre-filtrar condición/envío: se filtra aquí)."""
    new = [c for c in comparables if c.condition == "new"]
    free = [c for c in new if c.free_shipping]
    notes: list[str] = []

    pool = free
    if len(free) < min_comparables and len(new) > len(free):
        pool = new
        if free:
            notes.append(f"solo {len(free)} comparables con envío gratis; se usan {len(new)} nuevas en total")
        else:
            notes.append("ninguna comparable con envío gratis; p25 calculado sobre todas las nuevas")

    p25 = _p25([c.price for c in pool])
    base_candidates = [v for v in (buy_box_price, p25) if v is not None]
    liquidez_baja = len(pool) < min_comparables
    if liquidez_baja:
        notes.append(f"liquidez baja: {len(pool)} comparables (mínimo {min_comparables})")

    demand = demand_signal_from(comparables)
    if not base_candidates:
        notes.append("sin comparables ni buy box: no se puede fijar un precio de venta")
        return PricingResult(0.0, buy_box_price, p25, len(pool), True, demand, tuple(notes))

    sale_price = round(min(base_candidates) * (1 - undercut), 2)
    return PricingResult(sale_price, buy_box_price, p25, len(pool), liquidez_baja, demand, tuple(notes))


# ── peso (para el envío de compute_profit) ──────────────────────────────────────────────


def _weight_from_ml_attributes(raw_attrs: list[dict] | None) -> int | None:
    for a in raw_attrs or []:
        if a.get("id") != "WEIGHT":
            continue
        m = _WEIGHT_ATTR_RE.search(str(a.get("value_name") or "").lower())
        if m:
            n = float(m.group(1))
            return round(n * 1000) if m.group(2) == "kg" else round(n)
    return None


def resolve_weight_g(offer: Offer, listing: MLListing | None, default_weight_g: int) -> tuple[int, str]:
    """Prioridad: JSON-LD del retailer (lo más confiable: es EL producto que se compró) >
    atributo `WEIGHT` de la publicación de ML elegida > default configurado. Vive aquí (no en
    `scanner.py`) para que `paper/ledger.py` pueda reproducir EXACTAMENTE el mismo peso al
    re-marcar una posición sin importar circularidad de imports."""
    if offer.weight_g:
        return offer.weight_g, "jsonld"
    if listing is not None:
        w = _weight_from_ml_attributes(listing.raw.get("attributes") if listing.raw else None)
        if w:
            return w, "atributos"
    return default_weight_g, "default"
