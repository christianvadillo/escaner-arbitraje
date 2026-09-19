"""paper/ledger.py: abrir posiciones desde oportunidades reales del scanner, marcarlas a
mercado (re-cotizando con las MISMAS comparables), censura de fuentes sin vida observable,
JSON-LD con caché de 1/día y degradación ante bloqueo, y cierre al horizonte de 30 días."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from escaner import scanner, store
from escaner.config import Settings
from escaner.http import PoliteClient
from escaner.meli.client import MLClient
from escaner.meli.fake import FakeMeLiBackend, build_fake_http_client
from escaner.models import Offer
from escaner.paper import ledger
from escaner.sources.fixtures import FixturesSource, ml_catalog


@pytest.fixture
def ml_client() -> MLClient:
    items, products = ml_catalog()
    return MLClient(http=build_fake_http_client(FakeMeLiBackend(items, products)))


@pytest.fixture
def conn() -> sqlite3.Connection:
    return store.connect(":memory:")


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env({})


def _scan(ml_client, conn, settings):
    return scanner.scan(FixturesSource(), ml_client, conn, settings)


def test_scan_opens_one_position_per_opportunity(ml_client, conn, settings):
    summary = _scan(ml_client, conn, settings)
    positions = store.open_positions(conn)
    assert len(positions) == summary.n_opportunities
    for row in positions:
        assert row["closed_at"] is None
        assert row["capital"] > 0


def test_mark_all_marks_every_open_position(ml_client, conn, settings):
    summary = _scan(ml_client, conn, settings)
    n_marked = ledger.mark_all(conn, ml_client, settings)
    assert n_marked == summary.n_opportunities

    positions = store.positions_for_stats(conn)
    assert len(positions) == summary.n_opportunities
    for p in positions:
        assert p.marked_profit is not None
        assert p.deal_hours >= 0


def test_mark_with_unchanged_prices_keeps_profit_close_to_expected(ml_client, conn, settings):
    """Los fixtures no cambian de precio entre el scan y el mark: la utilidad marcada debe
    quedar muy cerca de la esperada (mismo cálculo, mismos insumos)."""
    _scan(ml_client, conn, settings)
    ledger.mark_all(conn, ml_client, settings)
    for p in store.positions_for_stats(conn):
        assert p.marked_profit == pytest.approx(p.expected_profit, rel=0.01)


def test_paper_summary_insufficient_with_few_positions(ml_client, conn, settings):
    _scan(ml_client, conn, settings)
    ledger.mark_all(conn, ml_client, settings)
    summary = ledger.paper_summary(conn)
    assert summary.verdict.startswith("INSUFICIENTE")
    assert summary.n_marked > 0


def test_mark_all_closes_positions_past_the_horizon(ml_client, conn, settings):
    _scan(ml_client, conn, settings)
    far_future = datetime.now(UTC) + timedelta(days=ledger.POSITION_HORIZON_DAYS + 1)
    ledger.mark_all(conn, ml_client, settings, now=far_future)
    assert store.open_positions(conn) == []


def test_promodescuentos_offers_are_always_censored():
    """Fuente sin vida observable: nunca se marca `deal_ended=True`, sin importar cuánto
    tiempo pase — la ausencia de evidencia no es evidencia de que murió."""
    offer = Offer(
        source="promodescuentos",
        merchant="X",
        title="t",
        price=1.0,
        url="https://www.promodescuentos.com/ofertas/1",
        guid="g1",
        published_at=datetime.now(UTC) - timedelta(hours=500),
    )
    result = ledger._observe(offer, keepa_checker=None, jsonld_checker=None)
    assert result is None


def test_jsonld_liveness_checker_detects_out_of_stock():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            text=(
                '<script type="application/ld+json">{"@type": "Product", "name": "x", '
                '"offers": {"@type": "Offer", "price": "1", "availability": "https://schema.org/OutOfStock"}}'
                "</script>"
            ),
        )

    client = PoliteClient(
        "ua-test", sqlite3.connect(":memory:"), min_host_interval_s=0.0, transport=httpx.MockTransport(handler)
    )
    checker = ledger.JsonLdLivenessChecker(client, frozenset({"costco.com.mx"}))
    offer = Offer(
        source="fixtures", merchant="Costco MX", title="t", price=1.0, url="https://www.costco.com.mx/p/1", guid="g2"
    )
    result = checker.check(offer)
    assert result is not None and result.alive is False


def test_jsonld_liveness_checker_rechecks_at_most_once_per_day():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(
            200,
            text=(
                '<script type="application/ld+json">{"@type": "Product", "name": "x", '
                '"offers": {"price": "1", "availability": "https://schema.org/InStock"}}</script>'
            ),
        )

    client = PoliteClient(
        "ua-test", sqlite3.connect(":memory:"), min_host_interval_s=0.0, transport=httpx.MockTransport(handler)
    )
    checker = ledger.JsonLdLivenessChecker(client, frozenset({"costco.com.mx"}))
    offer = Offer(
        source="fixtures", merchant="Costco MX", title="t", price=1.0, url="https://www.costco.com.mx/p/1", guid="g3"
    )

    first = checker.check(offer)
    second = checker.check(offer)  # mismo día: no debe volver a pedir la página
    assert first is not None
    assert second is None
    assert calls["n"] == 1


def test_jsonld_liveness_checker_degrades_gracefully_when_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(403, text="captcha")

    client = PoliteClient(
        "ua-test", sqlite3.connect(":memory:"), min_host_interval_s=0.0, transport=httpx.MockTransport(handler)
    )
    checker = ledger.JsonLdLivenessChecker(client, frozenset({"costco.com.mx"}))
    offer = Offer(
        source="fixtures", merchant="Costco MX", title="t", price=1.0, url="https://www.costco.com.mx/p/1", guid="g4"
    )
    assert checker.check(offer) is None  # bloqueado: se mantiene lo último conocido, no truena


def test_keepa_liveness_checker_reports_dead_when_no_current_price():
    class FakeKeepa:
        def current_price(self, asin):
            return None

    checker = ledger.KeepaLivenessChecker(FakeKeepa())
    offer = Offer(
        source="keepa", merchant="Amazon MX", title="t", price=1.0, url="https://amazon.com.mx/dp/X", guid="ASIN1"
    )
    result = checker.check(offer)
    assert result is not None and result.alive is False


def test_open_position_uses_detection_day_as_bootstrap_block(ml_client, conn, settings):
    summary = _scan(ml_client, conn, settings)
    assert summary.n_opportunities >= 1
    row = store.open_positions(conn)[0]
    assert row["detection_day"] == summary.opportunities[0].detected_at.date().isoformat()
