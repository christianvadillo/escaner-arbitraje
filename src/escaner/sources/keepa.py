"""keepa.py — Amazon México vía Keepa (API de pago, `domain=11`/`domainId=11`; ESPECIFICACION
§2-3). Requiere `keepa_key` del operador. No pasa por `PoliteClient`: es una API oficial
autenticada y medida por token, no una página scrapeada sujeta a robots.txt/crawl-delay.

Dos hechos verificados que cualquier parser de Keepa tiene que respetar o mentir sobre precios:

  - Los precios vienen en **centavos** de MXN y hay que dividir entre 100.
  - `-1` es el valor centinela de Keepa para "sin oferta en ese momento" — NO es un precio de
    cero, y tratarlo como tal inventaría liquidaciones al 100 %.

[estimado] La forma exacta de la respuesta de `/deal` y `/product` sigue la documentación
pública de Keepa (arreglos `csv`/`current` indexados por tipo de precio, índice 0 = Amazon);
no se verificó contra una llamada en vivo en esta sesión (política de cero red). Si el formato
real difiere, `parse_deal_response`/`parse_product_response` son el único lugar que hay que
tocar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from escaner.models import Offer

BASE_URL = "https://api.keepa.com"
DOMAIN_MX = 11
AMAZON_PRICE_TYPE = 0  # índice 0 de csv/current = precio de Amazon (no de terceros)
SOURCE_NAME = "keepa"


def cents_to_mxn(value: int | float | None) -> float | None:
    if value is None or value < 0:
        return None
    return round(value / 100.0, 2)


def _first_price(value: Any) -> float | None:
    """`current`/`csv` pueden venir como lista (por tipo de precio) o ya como el valor único."""
    if isinstance(value, list):
        value = value[AMAZON_PRICE_TYPE] if len(value) > AMAZON_PRICE_TYPE else None
    if not isinstance(value, int | float):
        return None
    return cents_to_mxn(value)


@dataclass(frozen=True)
class KeepaDealFilter:
    price_min_mxn: float | None = None
    price_max_mxn: float | None = None
    min_drop_percent: int = 20
    domain_id: int = DOMAIN_MX

    def to_selection(self) -> dict:
        sel: dict[str, Any] = {"domainId": self.domain_id, "priceTypes": [AMAZON_PRICE_TYPE]}
        price_range: dict[str, int] = {}
        if self.price_min_mxn is not None:
            price_range["min"] = round(self.price_min_mxn * 100)
        if self.price_max_mxn is not None:
            price_range["max"] = round(self.price_max_mxn * 100)
        if price_range:
            sel["priceRange"] = price_range
        if self.min_drop_percent:
            sel["deltaPercentRange"] = {"min": self.min_drop_percent, "max": 100}
        return sel


def parse_deal_response(payload: dict) -> tuple[list[Offer], int | None]:
    offers: list[Offer] = []
    for d in payload.get("deals") or []:
        asin = d.get("asin")
        title = d.get("title")
        price = _first_price(d.get("current"))
        if not asin or not title or price is None:
            continue
        offers.append(
            Offer(
                source=SOURCE_NAME,
                merchant="Amazon MX",
                title=title,
                price=price,
                url=f"https://www.amazon.com.mx/dp/{asin}",
                guid=asin,
                category=str(d.get("rootCategory")) if d.get("rootCategory") else None,
                raw=d,
            )
        )
    return offers, payload.get("tokensLeft")


def parse_product_response(payload: dict) -> tuple[list[Offer], int | None]:
    offers: list[Offer] = []
    for p in payload.get("products") or []:
        asin = p.get("asin")
        title = p.get("title")
        csv_rows = p.get("csv") or []
        price = (
            _first_price(csv_rows[AMAZON_PRICE_TYPE][-1])
            if len(csv_rows) > AMAZON_PRICE_TYPE and csv_rows[AMAZON_PRICE_TYPE]
            else None
        )
        if not asin or not title or price is None:
            continue
        eans = p.get("eanList") or []
        offers.append(
            Offer(
                source=SOURCE_NAME,
                merchant="Amazon MX",
                title=title,
                price=price,
                url=f"https://www.amazon.com.mx/dp/{asin}",
                guid=asin,
                gtin=eans[0] if eans else None,
                brand=p.get("brand"),
                model=p.get("model"),
                raw=p,
            )
        )
    return offers, payload.get("tokensLeft")


class KeepaSource:
    name = SOURCE_NAME

    def __init__(self, api_key: str, http_client: httpx.Client, deal_filter: KeepaDealFilter | None = None) -> None:
        self._key = api_key
        self._http = http_client
        self._filter = deal_filter or KeepaDealFilter()
        self.tokens_left: int | None = None

    def fetch(self, limit: int = 50) -> list[Offer]:
        params = {
            "key": self._key,
            "domain": self._filter.domain_id,
            "selection": json.dumps(self._filter.to_selection()),
        }
        resp = self._http.get(f"{BASE_URL}/deal", params=params)
        resp.raise_for_status()
        offers, tokens_left = parse_deal_response(resp.json())
        self.tokens_left = tokens_left
        return offers[:limit]

    def current_price(self, asin: str) -> float | None:
        """Para `paper/ledger.py`: re-cotiza un ASIN ya visto (vida de la oferta)."""
        resp = self._http.get(
            f"{BASE_URL}/product", params={"key": self._key, "domain": self._filter.domain_id, "asin": asin}
        )
        resp.raise_for_status()
        offers, tokens_left = parse_product_response(resp.json())
        self.tokens_left = tokens_left
        return offers[0].price if offers else None
