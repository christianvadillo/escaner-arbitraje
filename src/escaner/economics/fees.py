"""fees.py — comisión y costo de envío que Mercado Libre le cobra al vendedor (MLM).

Dos fuentes, en orden de preferencia:
  1. La API (`GET /sites/MLM/listing_prices` y `GET /users/{id}/shipping_options/free`),
     que requiere token de un vendedor. Es la única verdad: la comisión varía por categoría y
     tipo de publicación, y desde el 8-abr-2026 el cargo fijo depende de logistic_type,
     shipping_mode y peso facturable. Parsers abajo; el cliente HTTP vive en `escaner.meli`.
  2. Tablas por defecto para trabajar sin token. Son ESTIMACIONES y cada cotización lo dice
     (`source="tabla"`), para que ningún margen salga con más precisión de la que tiene.

Hechos verificados (sep-2026):
  - Tipos: gold_special = Clásica, gold_pro = Premium. Rangos publicados por terceros:
    Clásica ~10–15.5 %, Premium ~14.5–19.5 % (no hay tabla oficial abierta).
  - Envío gratis obligatorio desde $299 MXN (supermercado $499); abajo lo paga el comprador.
  - Cargo fijo con precio < $299: solo en Flex (self_service) y ME1/custom; en ME2 normal no.
  - Costo de envío para el vendedor, tramo hasta 0.3 kg y $299–498.99: lista $104.8;
    verde/MercadoLíder paga 50 % ($52.4), amarilla o sin reputación 40 % de descuento ($62.9),
    naranja/roja sin descuento.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import Protocol

FREE_SHIPPING_THRESHOLD = 299.0

LISTING_TYPES = {"gold_special": "Clásica", "gold_pro": "Premium"}

# Punto medio de los rangos publicados por terceros; reemplazar con la API o un CSV propio.
DEFAULT_PERCENTAGE = {"gold_special": 0.13, "gold_pro": 0.175}

# Escalera de cargo fijo previa a abr-2026 (blogs); solo aplica en las logísticas de abajo.
FIXED_FEE_TIERS = ((99.0, 25.0), (149.0, 30.0), (299.0, 37.0))
FIXED_FEE_LOGISTICS = frozenset({"self_service"})
FIXED_FEE_MODES = frozenset({"me1", "custom", "not_specified"})

# Costo de lista (antes de descuento por reputación) por peso facturable, MXN.
# Solo el primer tramo está verificado; el resto es ESTIMADO y debe reemplazarse.
SHIPPING_LIST_COST = (
    (300, 104.8),
    (500, 112.0),
    (1000, 124.0),
    (2000, 146.0),
    (3000, 168.0),
    (5000, 199.0),
    (7000, 239.0),
    (9000, 279.0),
    (12000, 339.0),
    (15000, 389.0),
    (20000, 469.0),
    (30000, 589.0),
)

REPUTATION_DISCOUNT = {
    "lider": 0.50,
    "green": 0.50,
    "light_green": 0.40,
    "yellow": 0.40,
    "none": 0.40,
    "orange": 0.0,
    "red": 0.0,
}


@dataclass(frozen=True)
class FeeQuote:
    percentage: float  # 0.13 = 13 %
    fixed_fee: float  # MXN por unidad
    financing_fee: float = 0.0  # cargo por ofrecer meses sin intereses
    source: str = "tabla"  # "api" | "tabla"

    def sale_fee(self, price: float) -> float:
        return self.percentage * price + self.fixed_fee + self.financing_fee


@dataclass(frozen=True)
class ShippingQuote:
    cost: float  # lo que paga el vendedor
    list_cost: float
    discount: float
    source: str = "tabla"


class FeeModel(Protocol):
    def quote(
        self,
        price: float,
        category_id: str | None = None,
        listing_type: str = "gold_special",
        logistic_type: str = "drop_off",
        shipping_mode: str = "me2",
        billable_weight_g: int | None = None,
    ) -> FeeQuote: ...


class ShippingModel(Protocol):
    def seller_cost(self, price: float, weight_g: int, reputation: str = "green") -> ShippingQuote: ...


class TableFeeModel:
    """Comisión por tabla: porcentaje por tipo de publicación (y por categoría si se da)."""

    def __init__(self, category_percentages: dict[tuple[str, str], float] | None = None) -> None:
        self.category_percentages = dict(category_percentages or {})

    def quote(
        self,
        price: float,
        category_id: str | None = None,
        listing_type: str = "gold_special",
        logistic_type: str = "drop_off",
        shipping_mode: str = "me2",
        billable_weight_g: int | None = None,
    ) -> FeeQuote:
        pct = self.category_percentages.get(
            (category_id or "", listing_type), DEFAULT_PERCENTAGE.get(listing_type, 0.15)
        )
        fixed = 0.0
        if price < FREE_SHIPPING_THRESHOLD and (
            logistic_type in FIXED_FEE_LOGISTICS or shipping_mode in FIXED_FEE_MODES
        ):
            for ceiling, amount in FIXED_FEE_TIERS:
                if price < ceiling:
                    fixed = amount
                    break
        return FeeQuote(percentage=pct, fixed_fee=fixed, source="tabla")


class TableShippingModel:
    """Envío por tabla de peso × descuento por reputación. Abajo de $299 lo paga el comprador."""

    def __init__(self, list_costs: tuple[tuple[int, float], ...] = SHIPPING_LIST_COST) -> None:
        self.weights = [w for w, _ in list_costs]
        self.costs = [c for _, c in list_costs]

    def seller_cost(self, price: float, weight_g: int, reputation: str = "green") -> ShippingQuote:
        if price < FREE_SHIPPING_THRESHOLD:
            return ShippingQuote(cost=0.0, list_cost=0.0, discount=0.0, source="comprador paga")
        idx = min(bisect_left(self.weights, max(1, weight_g)), len(self.costs) - 1)
        list_cost = self.costs[idx]
        discount = REPUTATION_DISCOUNT.get(reputation, 0.40)
        return ShippingQuote(cost=round(list_cost * (1 - discount), 2), list_cost=list_cost, discount=discount)


# ── Parsers de la API (el cliente HTTP los usa) ───────────────────────────────────────────


def fee_quote_from_listing_prices(payload: dict | list) -> FeeQuote:
    """Respuesta de `/sites/MLM/listing_prices` → FeeQuote. `percentage_fee` viene en puntos
    porcentuales (13 = 13 %); los montos en MXN."""
    row = payload[0] if isinstance(payload, list) else payload
    details = row.get("sale_fee_details") or {}
    pct = float(details.get("percentage_fee") or 0.0) / 100.0
    fixed = float(details.get("fixed_fee") or 0.0)
    financing = float(details.get("financing_add_on_fee") or 0.0)
    if not details and row.get("sale_fee_amount") is not None:
        # Sin desglose: todo el monto como fijo equivalente para ese precio.
        fixed = float(row["sale_fee_amount"])
    return FeeQuote(percentage=pct, fixed_fee=fixed, financing_fee=financing, source="api")


def shipping_quote_from_free_options(payload: dict) -> ShippingQuote:
    """Respuesta de `/users/{id}/shipping_options/free` → costo para el vendedor."""
    coverage = (payload.get("coverage") or {}).get("all_country") or {}
    list_cost = float(coverage.get("list_cost") or 0.0)
    discount_info = (payload.get("coverage") or {}).get("discount") or {}
    rate = float(discount_info.get("rate") or 0.0)
    rate = rate / 100.0 if rate > 1 else rate
    # `promoted_amount` no tiene semántica documentada (¿descuento o costo final?): no se usa.
    return ShippingQuote(cost=round(list_cost * (1 - rate), 2), list_cost=list_cost, discount=rate, source="api")
