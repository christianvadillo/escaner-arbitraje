"""Parseo del RSS de Promodescuentos: namespace `pepper` (con comodín de namespace), precios
tipo "$1,299.00", varios `pepper:merchant` por item (se queda con el más barato), items sin
precio se descartan."""

from __future__ import annotations

import sqlite3

import httpx

from escaner.http import PoliteClient
from escaner.sources.promodescuentos import RSS_URL, PromodescuentosSource, parse_rss

RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:pepper="http://www.pepper.com/rss/pepper.xsd" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>Promodescuentos</title>
    <item>
      <title>Licuadora Oster BLSTPYG1210 a $799 en Costco</title>
      <link>https://www.promodescuentos.com/ofertas/licuadora-oster-1</link>
      <guid isPermaLink="false">promo-guid-1</guid>
      <category>Hogar</category>
      <pubDate>Fri, 19 Sep 2026 10:00:00 +0000</pubDate>
      <description>&lt;p&gt;Buen precio en Costco&lt;/p&gt;</description>
      <pepper:merchant name="Costco MX" price="$799.00"/>
      <media:content url="https://img.example.com/oster.jpg"/>
    </item>
    <item>
      <title>Combo TV + soundbar (varias tiendas)</title>
      <link>https://www.promodescuentos.com/ofertas/combo-tv-2</link>
      <guid>promo-guid-2</guid>
      <category>Electrónica</category>
      <pubDate>Sat, 20 Sep 2026 08:30:00 +0000</pubDate>
      <pepper:merchant name="Tienda Cara" price="1,999.00"/>
      <pepper:merchant name="Tienda Barata" price="1,499.50"/>
    </item>
    <item>
      <title>Oferta sin precio (agotada)</title>
      <link>https://www.promodescuentos.com/ofertas/sin-precio-3</link>
      <guid>promo-guid-3</guid>
      <pepper:merchant name="Comercio X" price=""/>
    </item>
    <item>
      <title>Item sin ningun merchant</title>
      <link>https://www.promodescuentos.com/ofertas/sin-merchant-4</link>
      <guid>promo-guid-4</guid>
    </item>
  </channel>
</rss>
"""


def test_parse_rss_extracts_offers_with_pepper_namespace():
    offers = parse_rss(RSS_FIXTURE)
    assert len(offers) == 2  # las dos últimas se descartan (sin precio / sin merchant)

    first = offers[0]
    assert first.merchant == "Costco MX"
    assert first.price == 799.00
    assert first.title.startswith("Licuadora Oster")
    assert first.guid == "promo-guid-1"
    assert first.category == "Hogar"
    assert first.published_at is not None and first.published_at.year == 2026
    assert first.raw["media_url"] == "https://img.example.com/oster.jpg"


def test_parse_rss_keeps_cheapest_merchant():
    offers = parse_rss(RSS_FIXTURE)
    combo = next(o for o in offers if o.guid == "promo-guid-2")
    assert combo.merchant == "Tienda Barata"
    assert combo.price == 1499.50
    assert len(combo.raw["all_merchants"]) == 2


def test_promodescuentos_source_fetches_via_polite_client():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        assert str(request.url) == RSS_URL
        return httpx.Response(200, text=RSS_FIXTURE)

    client = PoliteClient(
        "escaner-arbitraje/0.1 (+test)",
        sqlite3.connect(":memory:"),
        min_host_interval_s=0.0,  # sin esto, robots.txt + la página real esperarían 10s de verdad
        transport=httpx.MockTransport(handler),
    )
    source = PromodescuentosSource(client)
    offers = source.fetch(limit=1)
    assert len(offers) == 1
    assert offers[0].source == "promodescuentos"
