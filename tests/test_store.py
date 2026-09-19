"""store.py: `init_db` es idempotente (PRAGMA user_version), upsert de offers por (source,
guid), la ventana de dedupe, y que runs/opportunities/paper_positions queden enlazados."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from escaner import store
from escaner.models import Offer


def test_init_db_is_idempotent():
    conn = sqlite3.connect(":memory:")
    store.init_db(conn)
    version_after_first = conn.execute("PRAGMA user_version").fetchone()[0]
    store.init_db(conn)  # no debe tronar ni duplicar nada
    version_after_second = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version_after_first == version_after_second == store.SCHEMA_VERSION

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for expected in (
        "offers",
        "listings",
        "opportunities",
        "paper_positions",
        "paper_marks",
        "runs",
        "http_cache",
        "judge_cache",
    ):
        assert expected in tables


def _offer(guid: str, price: float = 100.0) -> Offer:
    return Offer(source="csv", merchant="M", title="t", price=price, url="https://x/p", guid=guid)


def test_save_offer_upserts_by_source_and_guid():
    conn = store.connect(":memory:")
    id1 = store.save_offer(conn, _offer("g1", price=100.0))
    id2 = store.save_offer(conn, _offer("g1", price=150.0))  # mismo (source, guid): actualiza, no duplica
    assert id1 == id2
    row = conn.execute("SELECT price FROM offers WHERE id = ?", (id1,)).fetchone()
    assert row[0] == 150.0
    n = conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
    assert n == 1


def test_offer_seen_within_window():
    conn = store.connect(":memory:")
    store.save_offer(conn, _offer("g2"))
    assert store.offer_seen_within(conn, "csv", "g2", days=3) is True
    assert store.offer_seen_within(conn, "csv", "g2", days=0) is True  # visto "ahora mismo"
    assert store.offer_seen_within(conn, "csv", "otro-guid", days=3) is False


def test_run_lifecycle_is_tracked():
    conn = store.connect(":memory:")
    run_id = store.start_run(conn, "csv")
    row = conn.execute("SELECT finished_at FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert row[0] is None
    store.finish_run(conn, run_id, n_offers=5, n_opportunities=2, notes="ok")
    row = conn.execute(
        "SELECT finished_at, n_offers, n_opportunities, notes FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row[0] is not None
    assert (row[1], row[2], row[3]) == (5, 2, "ok")


def test_published_at_roundtrips_through_iso_format():
    conn = store.connect(":memory:")
    when = datetime(2026, 9, 19, 10, 0, tzinfo=UTC)
    offer_id = store.save_offer(
        conn, Offer(source="csv", merchant="M", title="t", price=1.0, url="https://x", guid="g3", published_at=when)
    )
    row = conn.execute("SELECT published_at FROM offers WHERE id = ?", (offer_id,)).fetchone()
    assert datetime.fromisoformat(row[0]) == when
