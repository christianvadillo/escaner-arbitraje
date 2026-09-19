"""csv_import.py — ofertas desde un CSV propio: `title, price, url, merchant, gtin, brand,
model, weight_g, category, guid`. Para cualquier fuente sobre la que el operador ya tenga
derecho a usar los datos (un scraping propio, una lista armada a mano, un export de otra
herramienta) sin tener que escribir un adaptador nuevo por cada una.
"""

from __future__ import annotations

import csv
from pathlib import Path

from escaner.models import Offer

REQUIRED_FIELDS = ("title", "price", "url")


def _parse_price(raw: str | None) -> float | None:
    if not raw:
        return None
    cleaned = raw.strip().replace("$", "").replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_csv_rows(rows: list[dict[str, str]], source_name: str = "csv") -> list[Offer]:
    offers: list[Offer] = []
    for row in rows:
        title = (row.get("title") or "").strip()
        url = (row.get("url") or "").strip()
        price = _parse_price(row.get("price"))
        if not title or not url or price is None:
            continue
        weight_raw = (row.get("weight_g") or "").strip()
        offers.append(
            Offer(
                source=source_name,
                merchant=(row.get("merchant") or "desconocido").strip(),
                title=title,
                price=price,
                url=url,
                guid=(row.get("guid") or url).strip(),
                gtin=(row.get("gtin") or "").strip() or None,
                brand=(row.get("brand") or "").strip() or None,
                model=(row.get("model") or "").strip() or None,
                weight_g=int(weight_raw) if weight_raw.isdigit() else None,
                category=(row.get("category") or "").strip() or None,
                raw=dict(row),
            )
        )
    return offers


def load_csv(path: str | Path, source_name: str = "csv") -> list[Offer]:
    with open(path, newline="", encoding="utf-8") as f:
        return parse_csv_rows(list(csv.DictReader(f)), source_name)


class CsvSource:
    name = "csv"

    def __init__(self, path: str | Path) -> None:
        self._path = path

    def fetch(self, limit: int = 50) -> list[Offer]:
        return load_csv(self._path, self.name)[:limit]
