"""Economía de una reventa: impuestos 2026, comisión/envío y utilidad neta."""

import pytest

from escaner.economics.fees import (
    TableFeeModel,
    TableShippingModel,
    fee_quote_from_listing_prices,
    shipping_quote_from_free_options,
)
from escaner.economics.profit import CostAssumptions, break_even_sale_price, compute_profit, max_purchase_price
from escaner.economics.taxes import PRESETS, compute_taxes


def test_platform_retentions_2026():
    t = compute_taxes(PRESETS["plataformas"], sale_price=1160, purchase_cost=580, ml_charges=150)
    assert t.isr == pytest.approx(25.0)  # 2.5 % de 1000
    assert t.iva == pytest.approx(80.0)  # 8 % de 1000


def test_no_rfc_is_brutal():
    t = compute_taxes(PRESETS["sin_rfc"], sale_price=1160, purchase_cost=580, ml_charges=150)
    assert t.total == pytest.approx(360.0)  # 20 % + 16 % de 1000


def test_resico_value_added_credits_purchase_and_fees():
    t = compute_taxes(PRESETS["resico"], sale_price=1160, purchase_cost=580, ml_charges=116)
    assert t.iva == pytest.approx(160 - 80 - 16)
    assert t.isr == pytest.approx(25.0)


def test_resico_without_cfdi_loses_credit():
    prof = PRESETS["resico"].with_(purchase_has_cfdi=False)
    t = compute_taxes(prof, sale_price=1160, purchase_cost=580, ml_charges=116)
    assert t.iva == pytest.approx(160 - 16)


def test_table_fee_fixed_only_for_flex_or_me1_below_threshold():
    m = TableFeeModel()
    assert m.quote(200, logistic_type="drop_off").fixed_fee == 0.0
    assert m.quote(120, logistic_type="self_service").fixed_fee == 30.0
    assert m.quote(350, logistic_type="self_service").fixed_fee == 0.0


def test_shipping_threshold_and_reputation():
    s = TableShippingModel()
    assert s.seller_cost(298, 300).cost == 0.0
    assert s.seller_cost(350, 300, "green").cost == pytest.approx(52.4)
    assert s.seller_cost(350, 300, "red").cost == pytest.approx(104.8)


def test_api_parsers():
    fq = fee_quote_from_listing_prices(
        [
            {
                "sale_fee_amount": 148.5,
                "sale_fee_details": {"percentage_fee": 13.5, "fixed_fee": 0, "financing_add_on_fee": 0},
            }
        ]
    )
    assert fq.percentage == pytest.approx(0.135) and fq.source == "api"
    sq = shipping_quote_from_free_options(
        {"coverage": {"all_country": {"list_cost": 104.8}, "discount": {"rate": 0.5}}}
    )
    assert sq.cost == pytest.approx(52.4)


def test_free_shipping_threshold_makes_profit_non_monotonic():
    below = compute_profit(298, 120, weight_g=300)
    above = compute_profit(305, 120, weight_g=300)
    assert below.net_profit > above.net_profit
    assert any("$298" in n for n in above.notes)


def test_profit_lines_add_up():
    p = compute_profit(2300, 1500, weight_g=800)
    total = sum(v for k, v in p.lines()[:-1])
    assert total == pytest.approx(p.net_profit)
    assert p.roi == pytest.approx(p.net_profit / (1500 + 15))


def test_break_even_is_tight():
    be = break_even_sale_price(1000, weight_g=800)
    assert be is not None
    assert compute_profit(be, 1000, weight_g=800).net_profit >= 0
    assert compute_profit(be - 1, 1000, weight_g=800).net_profit < 0


def test_max_purchase_price_hits_target_roi():
    c = max_purchase_price(2300, target_roi=0.25, weight_g=800)
    assert compute_profit(2300, c, weight_g=800).roi == pytest.approx(0.25, abs=0.005)


def test_no_rfc_kills_most_arbitrage():
    base = compute_profit(2300, 1500, weight_g=800)
    no_rfc = compute_profit(2300, 1500, weight_g=800, assumptions=CostAssumptions(tax=PRESETS["sin_rfc"]))
    assert no_rfc.net_profit < base.net_profit - 400
