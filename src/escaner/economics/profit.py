"""profit.py — utilidad NETA de comprar en un retailer y revender en Mercado Libre.

    utilidad = precio_venta
             − comisión ML (porcentaje + cargo fijo + financiamiento)
             − envío que paga el vendedor (≥ $299)
             − impuestos (retenciones de plataforma o IVA/ISR según régimen)
             − costo de compra (con IVA) − empaque
             − reserva de devoluciones (tasa × costo de una devolución)
             − costo de capital (dinero parado mientras se vende)

Todo en MXN y con IVA incluido donde el consumidor lo ve. El umbral de envío gratis hace la
función NO monótona: vender a $298 puede dejar más que a $305 porque desde $299 pagas el envío.
Por eso el precio de equilibrio se busca barriendo, no con bisección ciega, y el reporte avisa
cuando un precio queda justo arriba del umbral.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from escaner.economics.fees import (
    FREE_SHIPPING_THRESHOLD,
    FeeModel,
    ShippingModel,
    TableFeeModel,
    TableShippingModel,
)
from escaner.economics.taxes import PRESETS, TaxProfile, compute_taxes


@dataclass(frozen=True)
class CostAssumptions:
    """Supuestos del operador. Los defaults son conservadores y están a la vista."""

    listing_type: str = "gold_special"
    logistic_type: str = "drop_off"
    shipping_mode: str = "me2"
    reputation: str = "green"
    tax: TaxProfile = field(default_factory=lambda: PRESETS["plataformas"])
    packaging_cost: float = 15.0
    returns_rate: float = 0.04  # fracción de ventas que regresan
    return_loss_fraction: float = 0.25  # del costo de compra se pierde en una devolución
    capital_rate_annual: float = 0.15  # costo de oportunidad del dinero
    days_to_sell: float = 21.0
    source_shipping_cost: float = 0.0  # lo que cobra el retailer por enviarte la compra


@dataclass(frozen=True)
class ProfitBreakdown:
    sale_price: float
    purchase_cost: float
    sale_fee: float
    fee_percentage: float
    fixed_fee: float
    shipping_cost: float
    isr: float
    iva: float
    packaging: float
    returns_reserve: float
    capital_cost: float
    net_profit: float
    fee_source: str
    shipping_source: str
    tax_profile: str
    notes: tuple[str, ...] = ()

    @property
    def capital_invested(self) -> float:
        return self.purchase_cost + self.packaging

    @property
    def roi(self) -> float:
        return self.net_profit / self.capital_invested if self.capital_invested > 0 else 0.0

    @property
    def margin(self) -> float:
        return self.net_profit / self.sale_price if self.sale_price > 0 else 0.0

    def lines(self) -> list[tuple[str, float]]:
        return [
            ("Precio de venta", self.sale_price),
            (f"Comisión ML ({self.fee_percentage:.1%} + fijo)", -self.sale_fee),
            ("Envío (vendedor)", -self.shipping_cost),
            ("ISR", -self.isr),
            ("IVA", -self.iva),
            ("Compra (con IVA)", -self.purchase_cost),
            ("Empaque", -self.packaging),
            ("Reserva devoluciones", -self.returns_reserve),
            ("Costo de capital", -self.capital_cost),
            ("Utilidad neta", self.net_profit),
        ]


def compute_profit(
    sale_price: float,
    purchase_price: float,
    category_id: str | None = None,
    weight_g: int = 500,
    assumptions: CostAssumptions | None = None,
    fee_model: FeeModel | None = None,
    shipping_model: ShippingModel | None = None,
) -> ProfitBreakdown:
    a = assumptions or CostAssumptions()
    fee_model = fee_model or TableFeeModel()
    shipping_model = shipping_model or TableShippingModel()

    purchase_cost = purchase_price + a.source_shipping_cost
    fq = fee_model.quote(
        sale_price,
        category_id=category_id,
        listing_type=a.listing_type,
        logistic_type=a.logistic_type,
        shipping_mode=a.shipping_mode,
        billable_weight_g=weight_g,
    )
    sale_fee = fq.sale_fee(sale_price)
    sq = shipping_model.seller_cost(sale_price, weight_g, a.reputation)
    returns_reserve = a.returns_rate * (sq.cost + a.return_loss_fraction * purchase_cost)
    capital_cost = (purchase_cost + a.packaging_cost) * a.capital_rate_annual * a.days_to_sell / 365.0
    taxes = compute_taxes(
        a.tax,
        sale_price=sale_price,
        purchase_cost=purchase_cost,
        ml_charges=sale_fee + sq.cost,
        other_costs_pre_tax=a.packaging_cost + returns_reserve,
    )
    net = (
        sale_price
        - sale_fee
        - sq.cost
        - taxes.total
        - purchase_cost
        - a.packaging_cost
        - returns_reserve
        - capital_cost
    )
    notes = list(taxes.notes)
    if fq.source != "api":
        notes.append("comisión estimada por tabla (conecta la API para la real)")
    if sq.source == "tabla":
        notes.append("envío estimado por tabla de peso")
    if FREE_SHIPPING_THRESHOLD <= sale_price < FREE_SHIPPING_THRESHOLD * 1.08:
        notes.append("precio apenas arriba de $299: bajarlo a $298 elimina el envío que pagas")
    return ProfitBreakdown(
        sale_price=sale_price,
        purchase_cost=purchase_cost,
        sale_fee=sale_fee,
        fee_percentage=fq.percentage,
        fixed_fee=fq.fixed_fee,
        shipping_cost=sq.cost,
        isr=taxes.isr,
        iva=taxes.iva,
        packaging=a.packaging_cost,
        returns_reserve=returns_reserve,
        capital_cost=capital_cost,
        net_profit=net,
        fee_source=fq.source,
        shipping_source=sq.source,
        tax_profile=a.tax.name,
        notes=tuple(notes),
    )


def break_even_sale_price(
    purchase_price: float,
    category_id: str | None = None,
    weight_g: int = 500,
    assumptions: CostAssumptions | None = None,
    fee_model: FeeModel | None = None,
    shipping_model: ShippingModel | None = None,
    step: float = 1.0,
    max_multiple: float = 6.0,
) -> float | None:
    """Menor precio de venta con utilidad ≥ 0. Barrido de $1 (la función salta en $299) y
    refinamiento por bisección dentro del último escalón. None si ni a 6× la compra sale."""

    def profit(p: float) -> float:
        return compute_profit(
            p, purchase_price, category_id, weight_g, assumptions, fee_model, shipping_model
        ).net_profit

    p = max(step, purchase_price * 0.5)
    hi_limit = max(purchase_price * max_multiple, FREE_SHIPPING_THRESHOLD * 2)
    prev = p
    while p <= hi_limit:
        if profit(p) >= 0:
            lo, hi = prev, p
            if profit(lo) >= 0:
                return round(lo, 2)
            for _ in range(40):
                mid = (lo + hi) / 2
                if profit(mid) >= 0:
                    hi = mid
                else:
                    lo = mid
            return math.ceil(hi * 100) / 100  # redondear hacia arriba: nunca por debajo del equilibrio
        prev = p
        p += step
    return None


def max_purchase_price(
    sale_price: float,
    target_roi: float = 0.20,
    category_id: str | None = None,
    weight_g: int = 500,
    assumptions: CostAssumptions | None = None,
    fee_model: FeeModel | None = None,
    shipping_model: ShippingModel | None = None,
) -> float:
    """Lo máximo que puedes pagar en el retailer para lograr `target_roi` vendiendo a
    `sale_price`. La utilidad baja monótonamente con el costo de compra → bisección."""

    def roi(c: float) -> float:
        return compute_profit(sale_price, c, category_id, weight_g, assumptions, fee_model, shipping_model).roi

    lo, hi = 0.0, sale_price
    if roi(lo + 0.01) < target_roi:
        return 0.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if roi(mid) >= target_roi:
            lo = mid
        else:
            hi = mid
    return round(lo, 2)
