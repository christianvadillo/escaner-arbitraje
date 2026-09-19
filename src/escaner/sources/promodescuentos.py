"""promodescuentos.py — fuente RSS de Promodescuentos (ESPECIFICACION §3: uso personal; sus
términos prohíben reproducir el contenido en otro sitio, así que esto NO sirve para un SaaS sin
licencia). Feed RSS 2.0 con el namespace `pepper`, donde `pepper:merchant` trae los atributos
`name` y `price` del comercio que ofrece el precio marcado.

[supuesto] La URI exacta del namespace `pepper` no está fijada en la especificación (no se
verificó en vivo — política de cero red en esta construcción). Por eso el parser busca
`{*}merchant` con comodín de namespace (soportado por `xml.etree` desde Python 3.8): funciona
sea cual sea la URI real, y no se rompe si Promodescuentos la cambia.

[supuesto] `<link>` en este feed suele apuntar al hilo de Promodescuentos, no a la página del
retailer — por eso el enriquecimiento JSON-LD (`sources/jsonld.py`, que exige que el host esté
en `allowed_product_hosts`) rara vez aplica a ofertas de esta fuente en la práctica; ver
limitaciones en el README.
"""

from __future__ import annotations

import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from escaner.http import PoliteClient
from escaner.models import Offer

RSS_URL = "https://www.promodescuentos.com/rss"
SOURCE_NAME = "promodescuentos"

_PRICE_RE = re.compile(r"[\d.,]+")


def _parse_price(text: str | None) -> float | None:
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    # Formato MX: coma de miles, punto decimal ("$1,299.00"). Se asume ese formato — no se
    # observó un caso con coma decimal en el feed real (política de cero red).
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_pubdate(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None


def parse_rss(xml_text: str) -> list[Offer]:
    """XML crudo del feed → `Offer`. Descarta items sin ningún `pepper:merchant` con precio
    numérico: sin precio no hay oportunidad que costear. Si un item trae varios comercios, se
    queda con el más barato (es el que interesa para arbitraje)."""
    root = ET.fromstring(xml_text)  # noqa: S314 — XML propio del feed, no HTML de terceros
    offers: list[Offer] = []
    for item in root.iterfind(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        category = (item.findtext("category") or "").strip() or None
        description = (item.findtext("description") or "").strip() or None
        published_at = _parse_pubdate(item.findtext("pubDate"))

        best: tuple[float, str] | None = None
        all_merchants = []
        for m in item.findall("{*}merchant"):
            price = _parse_price(m.get("price"))
            name = m.get("name") or "desconocido"
            all_merchants.append({"name": name, "price": price})
            if price is not None and (best is None or price < best[0]):
                best = (price, name)
        if best is None:
            continue

        media = item.find("{*}content")
        offers.append(
            Offer(
                source=SOURCE_NAME,
                merchant=best[1],
                title=title,
                price=best[0],
                url=link,
                guid=guid or link,
                published_at=published_at,
                category=category,
                raw={
                    "description": description,
                    "media_url": media.get("url") if media is not None else None,
                    "all_merchants": all_merchants,
                },
            )
        )
    return offers


class PromodescuentosSource:
    name = SOURCE_NAME

    def __init__(self, client: PoliteClient, rss_url: str = RSS_URL) -> None:
        self._client = client
        self._rss_url = rss_url

    def fetch(self, limit: int = 50) -> list[Offer]:
        result = self._client.get(self._rss_url)
        return parse_rss(result.text)[:limit]
