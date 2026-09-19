"""report.py: reporte Markdown y CSV de oportunidades, y el reporte de papel — probados
directamente sobre objetos construidos a mano (sin pasar por scanner/store), para que un typo
en el formato de salida no dependa de que el pipeline completo haya corrido antes."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from escaner.economics.profit import CostAssumptions, compute_profit
from escaner.matching.score import MatchScore
from escaner.models import MLListing, Offer, Opportunity
from escaner.paper.stats import summarize
from escaner.report import opportunities_csv, opportunities_markdown, paper_report_markdown


def _opportunity(guid: str, net_profit_hint: float) -> Opportunity:
    offer = Offer(
        source="fixtures", merchant="Costco MX", title=f"Producto {guid}", price=1000.0, url="https://x/p", guid=guid
    )
    listing = MLListing(
        item_id=f"MLM-{guid}", title=f"Producto {guid} ML", price=net_profit_hint, permalink="https://ml/x"
    )
    profit = compute_profit(net_profit_hint, 1000.0, weight_g=500, assumptions=CostAssumptions())
    return Opportunity(
        offer=offer,
        listing=listing,
        match=MatchScore(0.95, "match", ("mismo código de modelo",)),
        comparables=(listing,),
        sale_price=net_profit_hint,
        profit=profit,
        flags=("liquidez_baja",) if net_profit_hint < 1300 else (),
        detected_at=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
    )


def test_opportunities_markdown_empty():
    out = opportunities_markdown([])
    assert "Ninguna oportunidad" in out


def test_opportunities_markdown_includes_breakdown_and_flags():
    opp = _opportunity("g1", 1800.0)
    out = opportunities_markdown([opp])
    assert "Producto g1" in out
    assert "Utilidad neta" in out
    assert "ROI:" in out
    assert "liquidez_baja" not in out  # este caso no lleva el flag (net_profit_hint >= 1300)


def test_opportunities_markdown_shows_flags_when_present():
    opp = _opportunity("g2", 1200.0)
    out = opportunities_markdown([opp])
    assert "Flags: liquidez_baja" in out


def test_opportunities_markdown_ranks_by_net_profit_descending():
    low = _opportunity("low", 1150.0)
    high = _opportunity("high", 1900.0)
    out = opportunities_markdown([low, high])
    assert out.index("Producto high") < out.index("Producto low")


def test_opportunities_markdown_respects_top_n():
    opps = [_opportunity(f"g{i}", 1300.0 + i) for i in range(5)]
    out = opportunities_markdown(opps, top_n=2)
    assert out.count("## ") == 2


def test_opportunities_csv_has_expected_columns_and_rows():
    opp = _opportunity("g3", 1800.0)
    raw = opportunities_csv([opp])
    rows = list(csv.reader(io.StringIO(raw)))
    header, row = rows[0], rows[1]
    assert header == [
        "offer_title",
        "merchant",
        "purchase_price",
        "ml_item_id",
        "sale_price",
        "net_profit",
        "roi",
        "match_prob",
        "decision",
        "flags",
        "detected_at",
    ]
    assert row[0] == "Producto g3"
    assert row[3] == "MLM-g3"
    assert row[8] == "match"


def test_paper_report_markdown_insufficient_verdict():
    out = paper_report_markdown(summarize([]))
    assert "SIN DATOS" in out
    assert "Vida mediana de la oferta: no calculable" in out
