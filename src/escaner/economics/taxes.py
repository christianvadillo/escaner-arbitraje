"""taxes.py — cuánto se lleva el fisco de cada venta en Mercado Libre (México, 2026).

Mercado Libre retiene impuestos a las personas físicas que venden en la plataforma
(art. 113-A LISR y 18-J LIVA). Tasas vigentes desde el 1-ene-2026 (LIF 2026, DOF 07-11-2025):

    ISR  con RFC 2.5 %  (era 1 % en 2025) | sin RFC 20 %      — sobre el precio SIN IVA
    IVA  con RFC 8 %    (50 % del IVA)    | sin RFC 16 %      — sobre el precio SIN IVA

Un escáner de arbitraje que ignora esto sobreestima el margen en ~10 puntos del precio (con
RFC) o ~31 puntos (sin RFC). Lo que cuesta de verdad depende del régimen:

  - "sin_rfc":      ISR 20 % + IVA 16 % retenidos y no recuperables.
  - "plataformas":  opción de pago definitivo (ingresos ≤ $300,000/año): la retención ES el
                    impuesto (2.5 % + 8 %); el IVA de la compra no se acredita.
  - "resico":       ISR sobre ingresos (≤ 2.5 %; la retención de 2.5 % lo cubre y se toma
                    como costo); IVA sobre valor agregado (se acredita el de la compra si
                    hay CFDI y el de las comisiones de ML).
  - "empresarial":  ISR sobre utilidad (tasa marginal configurable) + IVA sobre valor agregado.

No es asesoría fiscal: son supuestos explícitos para que el margen no mienta; valida el tuyo
con tu contador. Todos los precios de entrada INCLUYEN IVA, como los ve un consumidor.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

IVA = 0.16


@dataclass(frozen=True)
class TaxProfile:
    name: str
    isr_mode: str  # "revenue": tasa sobre ingreso sin IVA | "profit": tasa sobre utilidad
    isr_rate: float
    iva_mode: str  # "retention": la retención es definitiva | "value_added": 16 % del valor agregado
    iva_retention_rate: float = 0.08
    purchase_has_cfdi: bool = True  # en value_added: ¿el retailer te factura la compra?
    fees_include_iva: bool = True  # las comisiones/envíos de ML traen IVA incluido
    fees_creditable: bool = True  # en value_added: ML factura sus cargos (IVA acreditable)

    def with_(self, **kw) -> TaxProfile:
        return replace(self, **kw)


PRESETS: dict[str, TaxProfile] = {
    "sin_rfc": TaxProfile("sin_rfc", "revenue", 0.20, "retention", 0.16),
    "plataformas": TaxProfile("plataformas", "revenue", 0.025, "retention", 0.08),
    "resico": TaxProfile("resico", "revenue", 0.025, "value_added"),
    "empresarial": TaxProfile("empresarial", "profit", 0.30, "value_added"),
}


@dataclass(frozen=True)
class TaxBreakdown:
    isr: float
    iva: float  # IVA que efectivamente pagas (retenido definitivo o neto a enterar)
    notes: tuple[str, ...] = ()

    @property
    def total(self) -> float:
        return self.isr + self.iva


def net_of_iva(amount_with_iva: float) -> float:
    return amount_with_iva / (1.0 + IVA)


def iva_in(amount_with_iva: float) -> float:
    return amount_with_iva - net_of_iva(amount_with_iva)


def compute_taxes(
    profile: TaxProfile,
    sale_price: float,
    purchase_cost: float,
    ml_charges: float,
    other_costs_pre_tax: float = 0.0,
) -> TaxBreakdown:
    """Impuestos de UNA venta.

    sale_price    precio al comprador (con IVA)
    purchase_cost lo que pagaste al retailer (con IVA)
    ml_charges    comisiones + envío que te cobra ML (con o sin IVA según el perfil)
    other_costs_pre_tax  costos deducibles sin IVA acreditable (empaque, reserva de devoluciones)
    """
    net_sale = net_of_iva(sale_price)
    notes: list[str] = []

    # IVA ─────────────────────────────────────────────────────────────────────────────────
    if profile.iva_mode == "retention":
        iva = profile.iva_retention_rate * net_sale
        if profile.iva_retention_rate < IVA:
            notes.append(
                f"IVA: retención {profile.iva_retention_rate:.0%} definitiva; el IVA de la compra no se acredita"
            )
        else:
            notes.append("IVA: retención del 100 % (sin RFC); nada acreditable")
    elif profile.iva_mode == "value_added":
        iva_credit = 0.0
        if profile.purchase_has_cfdi:
            iva_credit += iva_in(purchase_cost)
        else:
            notes.append("IVA: compra sin CFDI → no acreditas su IVA")
        if profile.fees_creditable:
            iva_credit += iva_in(ml_charges) if profile.fees_include_iva else IVA * ml_charges
        iva = max(0.0, iva_in(sale_price) - iva_credit)
    else:
        raise ValueError(f"iva_mode desconocido: {profile.iva_mode}")

    # ISR ─────────────────────────────────────────────────────────────────────────────────
    if profile.isr_mode == "revenue":
        isr = profile.isr_rate * net_sale
    elif profile.isr_mode == "profit":
        if profile.iva_mode == "value_added":
            deductible = (
                (net_of_iva(purchase_cost) if profile.purchase_has_cfdi else purchase_cost)
                + (net_of_iva(ml_charges) if profile.fees_include_iva else ml_charges)
                + other_costs_pre_tax
            )
        else:
            deductible = purchase_cost + ml_charges + other_costs_pre_tax
        isr = max(0.0, profile.isr_rate * (net_sale - deductible))
    else:
        raise ValueError(f"isr_mode desconocido: {profile.isr_mode}")

    return TaxBreakdown(isr=isr, iva=iva, notes=tuple(notes))
