"""store.py — persistencia en sqlite (stdlib, sin ORM). Migraciones por `PRAGMA user_version`
para que `init_db` sea idempotente: correrlo sobre una base ya al día no hace nada, correrlo
sobre una vacía la deja lista, y añadir una migración nueva en el futuro es agregar una función
al diccionario `_MIGRATIONS`, no reescribir el esquema completo.

`http_cache` y `judge_cache` NO se declaran aquí: son dueñas de su propio esquema (`http.py` y
`judge.py`, vía `ensure_schema`) porque también necesitan crearse solas en los tests de esos
módulos sin levantar todo `store.py`. Aquí solo se las invoca, para que una base nueva las tenga
desde el primer `init_db`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from escaner import http as http_mod
from escaner import judge as judge_mod
from escaner.economics.profit import ProfitBreakdown
from escaner.matching.score import MatchScore
from escaner.models import MLListing, Offer, Opportunity
from escaner.paper.stats import PaperPosition

SCHEMA_VERSION = 1


def _migrate_v1(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            n_offers INTEGER NOT NULL DEFAULT 0,
            n_opportunities INTEGER NOT NULL DEFAULT 0,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            guid TEXT NOT NULL,
            merchant TEXT NOT NULL,
            title TEXT NOT NULL,
            price REAL NOT NULL,
            url TEXT NOT NULL,
            published_at TEXT,
            gtin TEXT,
            brand TEXT,
            model TEXT,
            weight_g INTEGER,
            category TEXT,
            raw TEXT NOT NULL DEFAULT '{}',
            first_seen_at TEXT NOT NULL,
            UNIQUE(source, guid)
        );

        CREATE TABLE IF NOT EXISTS listings (
            item_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            price REAL NOT NULL,
            condition TEXT NOT NULL,
            free_shipping INTEGER NOT NULL,
            listing_type TEXT NOT NULL,
            category_id TEXT,
            catalog_product_id TEXT,
            brand TEXT,
            gtin TEXT,
            model TEXT,
            sold_quantity INTEGER,
            permalink TEXT,
            seller_id TEXT,
            raw TEXT NOT NULL DEFAULT '{}',
            fetched_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offer_id INTEGER NOT NULL REFERENCES offers(id),
            item_id TEXT NOT NULL,
            prob REAL NOT NULL,
            decision TEXT NOT NULL,
            veto TEXT,
            reasons TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offer_id INTEGER NOT NULL REFERENCES offers(id),
            item_id TEXT NOT NULL,
            match_prob REAL NOT NULL,
            match_decision TEXT NOT NULL,
            match_reasons TEXT NOT NULL DEFAULT '[]',
            comparables TEXT NOT NULL DEFAULT '[]',
            sale_price REAL NOT NULL,
            buy_box_price REAL,
            breakdown TEXT NOT NULL,
            flags TEXT NOT NULL DEFAULT '[]',
            weight_source TEXT NOT NULL DEFAULT 'default',
            detected_at TEXT NOT NULL,
            UNIQUE(offer_id, item_id)
        );

        CREATE TABLE IF NOT EXISTS paper_positions (
            position_id TEXT PRIMARY KEY,
            opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
            detection_day TEXT NOT NULL,
            capital REAL NOT NULL,
            expected_profit REAL NOT NULL,
            opened_at TEXT NOT NULL,
            closed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS paper_marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id TEXT NOT NULL REFERENCES paper_positions(position_id),
            marked_at TEXT NOT NULL,
            ml_price REAL,
            marked_profit REAL,
            deal_hours REAL NOT NULL,
            deal_ended INTEGER NOT NULL,
            days_marked REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_paper_marks_position ON paper_marks(position_id, marked_at);
        """
    )


_MIGRATIONS = {1: _migrate_v1}


def init_db(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version in sorted(_MIGRATIONS):
        if version > current:
            _MIGRATIONS[version](conn)
            conn.execute(f"PRAGMA user_version = {version}")
    http_mod.ensure_schema(conn)
    judge_mod.ensure_schema(conn)
    conn.commit()


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    init_db(conn)
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── runs ─────────────────────────────────────────────────────────────────────────────────


def start_run(conn: sqlite3.Connection, source: str) -> int:
    cur = conn.execute("INSERT INTO runs (source, started_at) VALUES (?, ?)", (source, _now()))
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, n_offers: int, n_opportunities: int, notes: str = "") -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, n_offers = ?, n_opportunities = ?, notes = ? WHERE id = ?",
        (_now(), n_offers, n_opportunities, notes, run_id),
    )
    conn.commit()


# ── offers / dedupe ─────────────────────────────────────────────────────────────────────


def save_offer(conn: sqlite3.Connection, offer: Offer) -> int:
    row = conn.execute(
        """
        INSERT INTO offers (source, guid, merchant, title, price, url, published_at, gtin, brand, model,
                             weight_g, category, raw, first_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, guid) DO UPDATE SET
            price = excluded.price, title = excluded.title, url = excluded.url
        RETURNING id
        """,
        (
            offer.source,
            offer.guid,
            offer.merchant,
            offer.title,
            offer.price,
            offer.url,
            offer.published_at.isoformat() if offer.published_at else None,
            offer.gtin,
            offer.brand,
            offer.model,
            offer.weight_g,
            offer.category,
            json.dumps(offer.raw, default=str),
            _now(),
        ),
    ).fetchone()
    conn.commit()
    return int(row[0])


def offer_seen_within(conn: sqlite3.Connection, source: str, guid: str, days: float) -> bool:
    row = conn.execute(
        "SELECT first_seen_at FROM offers WHERE source = ? AND guid = ? "
        "AND julianday('now') - julianday(first_seen_at) <= ?",
        (source, guid, days),
    ).fetchone()
    return row is not None


# ── listings / matches ──────────────────────────────────────────────────────────────────


def save_listing(conn: sqlite3.Connection, listing: MLListing) -> None:
    conn.execute(
        """
        INSERT INTO listings (item_id, title, price, condition, free_shipping, listing_type, category_id,
                               catalog_product_id, brand, gtin, model, sold_quantity, permalink, seller_id,
                               raw, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            price = excluded.price, fetched_at = excluded.fetched_at
        """,
        (
            listing.item_id,
            listing.title,
            listing.price,
            listing.condition,
            int(listing.free_shipping),
            listing.listing_type,
            listing.category_id,
            listing.catalog_product_id,
            listing.brand,
            listing.gtin,
            listing.model,
            listing.sold_quantity,
            listing.permalink,
            listing.seller_id,
            json.dumps(listing.raw, default=str),
            _now(),
        ),
    )
    conn.commit()


def save_match(conn: sqlite3.Connection, offer_id: int, listing: MLListing, score: MatchScore) -> None:
    conn.execute(
        "INSERT INTO matches (offer_id, item_id, prob, decision, veto, reasons, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (offer_id, listing.item_id, score.prob, score.decision, score.veto, json.dumps(list(score.reasons)), _now()),
    )
    conn.commit()


# ── opportunities ────────────────────────────────────────────────────────────────────────


def save_opportunity(conn: sqlite3.Connection, offer_id: int, opportunity: Opportunity) -> int:
    save_listing(conn, opportunity.listing)
    for c in opportunity.comparables:
        save_listing(conn, c)
    row = conn.execute(
        """
        INSERT INTO opportunities (offer_id, item_id, match_prob, match_decision, match_reasons, comparables,
                                    sale_price, buy_box_price, breakdown, flags, weight_source, detected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(offer_id, item_id) DO UPDATE SET sale_price = excluded.sale_price
        RETURNING id
        """,
        (
            offer_id,
            opportunity.listing.item_id,
            opportunity.match.prob,
            opportunity.match.decision,
            json.dumps(list(opportunity.match.reasons)),
            json.dumps([c.item_id for c in opportunity.comparables]),
            opportunity.sale_price,
            opportunity.buy_box_price,
            json.dumps(asdict(opportunity.profit)),
            json.dumps(list(opportunity.flags)),
            opportunity.weight_source,
            opportunity.detected_at.isoformat(),
        ),
    ).fetchone()
    conn.commit()
    return int(row[0])


def _offer_from_row(row: sqlite3.Row) -> Offer:
    return Offer(
        source=row["source"],
        merchant=row["merchant"],
        title=row["title"],
        price=row["price"],
        url=row["url"],
        guid=row["guid"],
        published_at=datetime.fromisoformat(row["published_at"]) if row["published_at"] else None,
        gtin=row["gtin"],
        brand=row["brand"],
        model=row["model"],
        weight_g=row["weight_g"],
        category=row["category"],
        raw=json.loads(row["raw"]) if row["raw"] else {},
    )


def _listing_from_row(row: sqlite3.Row) -> MLListing:
    return MLListing(
        item_id=row["item_id"],
        title=row["title"],
        price=row["price"],
        condition=row["condition"],
        free_shipping=bool(row["free_shipping"]),
        listing_type=row["listing_type"],
        category_id=row["category_id"],
        catalog_product_id=row["catalog_product_id"],
        brand=row["brand"],
        gtin=row["gtin"],
        model=row["model"],
        sold_quantity=row["sold_quantity"],
        permalink=row["permalink"],
        seller_id=row["seller_id"],
        raw=json.loads(row["raw"]) if row["raw"] else {},
    )


def load_opportunity(conn: sqlite3.Connection, opportunity_id: int) -> Opportunity:
    row = conn.execute(
        "SELECT o.*, off.* FROM opportunities o JOIN offers off ON off.id = o.offer_id WHERE o.id = ?",
        (opportunity_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"no existe la oportunidad {opportunity_id}")
    listing_row = conn.execute("SELECT * FROM listings WHERE item_id = ?", (row["item_id"],)).fetchone()
    comparable_ids: Iterable[str] = json.loads(row["comparables"])
    comparables = []
    for cid in comparable_ids:
        crow = conn.execute("SELECT * FROM listings WHERE item_id = ?", (cid,)).fetchone()
        if crow:
            comparables.append(_listing_from_row(crow))
    breakdown = ProfitBreakdown(**json.loads(row["breakdown"]))
    match = MatchScore(row["match_prob"], row["match_decision"], tuple(json.loads(row["match_reasons"])))
    return Opportunity(
        offer=_offer_from_row(row),
        listing=_listing_from_row(listing_row),
        match=match,
        comparables=tuple(comparables),
        sale_price=row["sale_price"],
        profit=breakdown,
        flags=tuple(json.loads(row["flags"])),
        detected_at=datetime.fromisoformat(row["detected_at"]),
        weight_source=row["weight_source"],
        buy_box_price=row["buy_box_price"],
    )


def list_recent_opportunities(conn: sqlite3.Connection, limit: int = 50) -> list[Opportunity]:
    rows = conn.execute("SELECT id FROM opportunities ORDER BY detected_at DESC LIMIT ?", (limit,)).fetchall()
    return [load_opportunity(conn, r["id"]) for r in rows]


# ── posiciones de papel ──────────────────────────────────────────────────────────────────


def open_paper_position(
    conn: sqlite3.Connection, opportunity_id: int, position: PaperPosition, opened_at: str | None = None
) -> None:
    conn.execute(
        "INSERT INTO paper_positions (position_id, opportunity_id, detection_day, capital, expected_profit, opened_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            position.position_id,
            opportunity_id,
            position.detection_day,
            position.capital,
            position.expected_profit,
            opened_at or _now(),
        ),
    )
    conn.commit()


def save_paper_mark(
    conn: sqlite3.Connection,
    position_id: str,
    ml_price: float | None,
    marked_profit: float | None,
    deal_hours: float,
    deal_ended: bool,
    days_marked: float,
) -> None:
    conn.execute(
        "INSERT INTO paper_marks "
        "(position_id, marked_at, ml_price, marked_profit, deal_hours, deal_ended, days_marked) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (position_id, _now(), ml_price, marked_profit, deal_hours, int(deal_ended), days_marked),
    )
    conn.commit()


def close_position(conn: sqlite3.Connection, position_id: str) -> None:
    conn.execute(
        "UPDATE paper_positions SET closed_at = ? WHERE position_id = ? AND closed_at IS NULL", (_now(), position_id)
    )
    conn.commit()


def open_positions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM paper_positions WHERE closed_at IS NULL").fetchall()


def positions_for_stats(conn: sqlite3.Connection) -> list[PaperPosition]:
    """Cada posición con su marca MÁS RECIENTE (o `marked_profit=None` si nunca se marcó)."""
    rows = conn.execute(
        """
        SELECT p.position_id, p.detection_day, p.capital, p.expected_profit,
               m.marked_profit, m.deal_hours, m.deal_ended
        FROM paper_positions p
        LEFT JOIN paper_marks m ON m.id = (
            SELECT id FROM paper_marks WHERE position_id = p.position_id ORDER BY marked_at DESC LIMIT 1
        )
        """
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            PaperPosition(
                position_id=r["position_id"],
                detection_day=r["detection_day"],
                capital=r["capital"],
                expected_profit=r["expected_profit"],
                marked_profit=r["marked_profit"],
                deal_hours=r["deal_hours"] if r["deal_hours"] is not None else 0.0,
                deal_ended=bool(r["deal_ended"]) if r["deal_ended"] is not None else False,
            )
        )
    return out


__all__ = [
    "SCHEMA_VERSION",
    "close_position",
    "connect",
    "finish_run",
    "init_db",
    "list_recent_opportunities",
    "load_opportunity",
    "offer_seen_within",
    "open_paper_position",
    "open_positions",
    "positions_for_stats",
    "save_match",
    "save_listing",
    "save_offer",
    "save_opportunity",
    "save_paper_mark",
    "start_run",
]
