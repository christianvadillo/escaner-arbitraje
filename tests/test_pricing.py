"""pricing.py: min(buy_box, p25) × (1 − undercut), filtro a nuevas+envío gratis, degradación a
"todas las nuevas" si no hay suficientes con envío gratis, `liquidez_baja`, señal de demanda
débil desde sold_quantity, y `resolve_weight_g` (JSON-LD > atributos ML > default)."""

from __future__ import annotations

from escaner.models import MLListing, Offer
from escaner.pricing import realistic_sale_price, resolve_weight_g


def _listing(item_id: str, price: float, *, condition: str = "new", free_shipping: bool = True, sold=None) -> MLListing:
    return MLListing(
        item_id=item_id,
        title=f"item {item_id}",
        price=price,
        condition=condition,
        free_shipping=free_shipping,
        sold_quantity=sold,
    )


def test_sale_price_is_min_of_buy_box_and_p25_with_undercut():
    comps = [_listing("a", 1000), _listing("b", 1100), _listing("c", 1200), _listing("d", 1300)]
    # p25 (method="inclusive") de [1000,1100,1200,1300] = 1075
    r = realistic_sale_price(comps, buy_box_price=1050, undercut=0.02)
    assert r.p25_price == 1075
    assert r.sale_price == round(min(1050, 1075) * 0.98, 2)
    assert r.liquidez_baja is False
    assert r.n_comparables == 4


def test_buy_box_can_be_the_binding_constraint():
    comps = [_listing("a", 2000), _listing("b", 2100), _listing("c", 2200)]
    r = realistic_sale_price(comps, buy_box_price=1500, undercut=0.0)
    assert r.sale_price == 1500.0


def test_filters_to_new_with_free_shipping_first():
    comps = [
        _listing("used", 500, condition="used"),
        _listing("paid_ship", 900, free_shipping=False),
        _listing("a", 1000),
        _listing("b", 1100),
        _listing("c", 1200),
    ]
    r = realistic_sale_price(comps, undercut=0.0)
    assert r.n_comparables == 3  # solo las 3 nuevas con envío gratis
    assert r.liquidez_baja is False


def test_falls_back_to_all_new_when_free_shipping_pool_too_small():
    comps = [_listing("a", 1000, free_shipping=False), _listing("b", 1100), _listing("used", 200, condition="used")]
    r = realistic_sale_price(comps, undercut=0.0, min_comparables=3)
    assert r.n_comparables == 2  # las 2 nuevas (con y sin envío gratis); la usada queda fuera
    assert r.liquidez_baja is True
    assert any("envío gratis" in n for n in r.notes)


def test_liquidez_baja_below_min_comparables():
    comps = [_listing("a", 1000), _listing("b", 1100)]
    r = realistic_sale_price(comps, undercut=0.0, min_comparables=3)
    assert r.liquidez_baja is True
    assert r.sale_price > 0


def test_no_comparables_and_no_buy_box_returns_zero_price():
    r = realistic_sale_price([], buy_box_price=None)
    assert r.sale_price == 0.0
    assert r.liquidez_baja is True
    assert "no se puede fijar" in " ".join(r.notes)


def test_single_comparable_p25_is_its_own_price():
    r = realistic_sale_price([_listing("a", 999)], undercut=0.0)
    assert r.p25_price == 999


def test_demand_signal_averages_available_sold_quantities():
    comps = [_listing("a", 100, sold=10), _listing("b", 100, sold=30), _listing("c", 100, sold=None)]
    r = realistic_sale_price(comps, undercut=0.0)
    assert r.demand_signal == 20.0


def test_demand_signal_none_when_nothing_reported():
    r = realistic_sale_price([_listing("a", 100)], undercut=0.0)
    assert r.demand_signal is None


# ── resolve_weight_g ─────────────────────────────────────────────────────────────────────


def _offer(**kw) -> Offer:
    base = dict(source="fixtures", merchant="X", title="t", price=1.0, url="https://x.example/p", guid="g")
    base.update(kw)
    return Offer(**base)


def test_weight_prefers_offer_jsonld_value():
    offer = _offer(weight_g=500)
    listing = MLListing(item_id="i", title="t", price=1.0, raw={"attributes": [{"id": "WEIGHT", "value_name": "9 kg"}]})
    g, source = resolve_weight_g(offer, listing, default_weight_g=1000)
    assert (g, source) == (500, "jsonld")


def test_weight_falls_back_to_ml_attribute():
    offer = _offer()
    listing = MLListing(
        item_id="i", title="t", price=1.0, raw={"attributes": [{"id": "WEIGHT", "value_name": "2.5 kg"}]}
    )
    g, source = resolve_weight_g(offer, listing, default_weight_g=1000)
    assert (g, source) == (2500, "atributos")


def test_weight_falls_back_to_default():
    offer = _offer()
    g, source = resolve_weight_g(offer, None, default_weight_g=1234)
    assert (g, source) == (1234, "default")
