"""E2E del escáner completo sobre los fixtures: fuente → candidatos → precio → utilidad →
umbrales → persistencia — SIN red (fixtures + `meli.fake`). Verifica el compromiso central de
la especificación: encuentra oportunidades reales de verdad Y rechaza los 4 señuelos (funda,
Pro-vs-no-Pro, paquete de 2, reacondicionado) con el veto correcto, no por accidente."""

from __future__ import annotations

import sqlite3

import pytest

from escaner import scanner, store
from escaner.config import Settings
from escaner.meli.client import MLClient
from escaner.meli.fake import FakeMeLiBackend, build_fake_http_client
from escaner.sources.fixtures import DECOY_GUIDS, FixturesSource, ml_catalog


@pytest.fixture
def ml_client() -> MLClient:
    items, products = ml_catalog()
    return MLClient(http=build_fake_http_client(FakeMeLiBackend(items, products)))


@pytest.fixture
def conn() -> sqlite3.Connection:
    return store.connect(":memory:")


def test_scan_finds_at_least_one_real_opportunity(ml_client, conn):
    settings = Settings.from_env({})
    summary = scanner.scan(FixturesSource(), ml_client, conn, settings)
    assert summary.n_offers == 15
    assert summary.n_opportunities >= 1
    assert len(summary.opportunities) == summary.n_opportunities
    for opp in summary.opportunities:
        assert opp.profit.net_profit >= settings.min_profit
        assert opp.profit.roi >= settings.min_roi
        assert opp.match.decision == "match"


def test_scan_rejects_all_four_decoys(ml_client, conn):
    settings = Settings.from_env({})
    summary = scanner.scan(FixturesSource(), ml_client, conn, settings)
    opportunity_guids = {o.offer.guid for o in summary.opportunities}
    assert opportunity_guids.isdisjoint(DECOY_GUIDS)


@pytest.mark.parametrize(
    ("guid", "expected_veto_fragment"),
    [
        ("fx-decoy-funda", "accesorio"),
        ("fx-decoy-iphone-refurb", "condición distinta"),
        ("fx-decoy-termo-pack", "paquete"),
        ("fx-decoy-buds-nonpro", "variante distinta"),
    ],
)
def test_each_decoy_is_found_and_vetoed_for_the_right_reason(ml_client, guid, expected_veto_fragment):
    """No basta con que el señuelo no aparezca como oportunidad: tiene que encontrar el
    candidato de verdad (misma marca/modelo que la oportunidad real) y vetarlo por la razón
    correcta — si no encontrara nada que puntuar, el veto no estaría probando nada."""
    from escaner.candidates import find_candidates
    from escaner.sources.fixtures import offers as fixture_offers

    settings = Settings.from_env({})
    offer = next(o for o in fixture_offers() if o.guid == guid)
    cr = find_candidates(offer, ml_client, settings.match_threshold, settings.review_threshold)
    assert not cr.matches and not cr.review  # todo lo que encontró se vetó a no_match

    # Reconstruir el veto exacto contra el candidato más cercano en texto.
    from escaner.matching.normalize import features
    from escaner.matching.score import score_pair

    query = offer.brand or offer.title
    raw_candidates = ml_client.search(query, limit=20, condition="new")
    assert raw_candidates, f"la búsqueda para {guid!r} no encontró ni un candidato crudo"
    full = ml_client.items([c.item_id for c in raw_candidates])
    offer_feat = features(offer.title, brand=offer.brand, gtin=offer.gtin, model=offer.model)
    vetoes = [
        score_pair(offer_feat, features(c.title, brand=c.brand, gtin=c.gtin, model=c.model, condition=c.condition)).veto
        for c in full
    ]
    assert any(v and expected_veto_fragment in v for v in vetoes), vetoes


def test_scan_persists_offers_and_opportunities(ml_client, conn):
    settings = Settings.from_env({})
    summary = scanner.scan(FixturesSource(), ml_client, conn, settings)
    n_offers_in_db = conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
    n_opps_in_db = conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
    assert n_offers_in_db == 15
    assert n_opps_in_db == summary.n_opportunities
    n_positions = conn.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]
    assert n_positions == summary.n_opportunities  # cada oportunidad abre su posición de papel


def test_scan_deduplicates_offers_seen_within_the_window(ml_client, conn):
    settings = Settings.from_env({})
    first = scanner.scan(FixturesSource(), ml_client, conn, settings)
    second = scanner.scan(FixturesSource(), ml_client, conn, settings)
    assert second.n_deduped == 15  # las mismas 15 (source, guid) ya vistas hace segundos
    assert second.n_opportunities == 0
    assert first.n_opportunities >= 1
