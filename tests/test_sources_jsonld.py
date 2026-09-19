"""JSON-LD: extracción de `Product` (con y sin `@graph`), gtin/brand/model/peso, y que
`enrich_offer` SOLO toque hosts en `allowed_product_hosts` (ESPECIFICACION §3)."""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from escaner.http import PoliteClient
from escaner.models import Offer
from escaner.sources.jsonld import enrich_offer, extract_product_jsonld, host_allowed

HTML_SIMPLE = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "Product",
  "name": "Licuadora Oster BLSTPYG1210",
  "gtin13": "7501055363056",
  "brand": {"@type": "Brand", "name": "Oster"},
  "sku": "BLSTPYG1210",
  "weight": {"@type": "QuantitativeValue", "value": "1.8", "unitCode": "KGM"},
  "offers": {"@type": "Offer", "price": "799.00", "availability": "https://schema.org/InStock"}
}
</script>
</head><body></body></html>
"""

HTML_GRAPH = """
<html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [
  {"@type": "BreadcrumbList", "itemListElement": []},
  {"@type": "Product", "name": "Termo Stanley", "gtin": "0194253401698", "weight": "0.9 kg",
   "offers": {"@type": "Offer", "price": "349", "availability": "https://schema.org/OutOfStock"}}
]}
</script>
</head><body></body></html>
"""

HTML_NO_PRODUCT = '<html><body><script type="application/ld+json">{"@type": "WebPage"}</script></body></html>'


def test_extract_product_jsonld_simple():
    p = extract_product_jsonld(HTML_SIMPLE)
    assert p is not None
    assert p["name"] == "Licuadora Oster BLSTPYG1210"


def test_extract_product_jsonld_from_graph():
    p = extract_product_jsonld(HTML_GRAPH)
    assert p is not None
    assert p["gtin"] == "0194253401698"


def test_extract_product_jsonld_returns_none_without_product():
    assert extract_product_jsonld(HTML_NO_PRODUCT) is None


def test_host_allowed_strips_www():
    hosts = frozenset({"costco.com.mx"})
    assert host_allowed("https://www.costco.com.mx/p/1", hosts)
    assert host_allowed("https://costco.com.mx/p/1", hosts)
    assert not host_allowed("https://otratienda.com.mx/p/1", hosts)


def _polite_client(handler) -> PoliteClient:
    # min_host_interval_s=0: cada prueba solo abre una PoliteClient nueva, pero robots.txt +
    # la página real ya son DOS peticiones al mismo host dentro de un solo enrich_offer() —
    # sin esto, el límite de tasa por defecto (10s) haría una espera de verdad por prueba.
    return PoliteClient(
        "escaner-arbitraje/0.1 (+test)",
        sqlite3.connect(":memory:"),
        min_host_interval_s=0.0,
        transport=httpx.MockTransport(handler),
    )


def test_enrich_offer_fills_missing_fields_from_allowed_host():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=HTML_SIMPLE)

    offer = Offer(
        source="fixtures",
        merchant="Costco MX",
        title="Licuadora Oster",
        price=799.0,
        url="https://www.costco.com.mx/p/licuadora-oster",
        guid="g1",
    )
    enriched = enrich_offer(offer, _polite_client(handler), frozenset({"costco.com.mx"}))
    assert enriched.gtin == "7501055363056"
    assert enriched.brand == "Oster"
    assert enriched.model == "BLSTPYG1210"
    assert enriched.weight_g == 1800


def test_enrich_offer_skips_hosts_outside_allowlist_without_any_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, text=HTML_SIMPLE)

    offer = Offer(
        source="fixtures",
        merchant="Otro",
        title="X",
        price=1.0,
        url="https://www.otratienda.com.mx/p/x",
        guid="g2",
    )
    enriched = enrich_offer(offer, _polite_client(handler), frozenset({"costco.com.mx"}))
    assert enriched is offer  # sin cambios: ni se intentó
    assert calls == []


def test_enrich_offer_never_overwrites_explicit_data():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=HTML_SIMPLE)

    offer = Offer(
        source="fixtures",
        merchant="Costco MX",
        title="Licuadora Oster",
        price=799.0,
        url="https://www.costco.com.mx/p/licuadora-oster",
        guid="g3",
        brand="Marca Explicita",  # no debe pisarse aunque el JSON-LD diga "Oster"
    )
    enriched = enrich_offer(offer, _polite_client(handler), frozenset({"costco.com.mx"}))
    assert enriched.brand == "Marca Explicita"
    assert enriched.gtin == "7501055363056"  # este sí se completa (no venía explícito)


def test_enrich_offer_returns_unchanged_when_page_has_no_product():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=HTML_NO_PRODUCT)

    offer = Offer(
        source="fixtures",
        merchant="Costco MX",
        title="X",
        price=1.0,
        url="https://www.costco.com.mx/p/rara",
        guid="g4",
    )
    enriched = enrich_offer(offer, _polite_client(handler), frozenset({"costco.com.mx"}))
    assert enriched.gtin is None


@pytest.mark.parametrize("weight_raw,unit,expected", [("1.8", "KGM", 1800), ("500", "g", 500)])
def test_weight_units(weight_raw, unit, expected):
    html = HTML_SIMPLE.replace('"value": "1.8", "unitCode": "KGM"', f'"value": "{weight_raw}", "unitCode": "{unit}"')
    p = extract_product_jsonld(html)
    from escaner.sources.jsonld import _weight_grams

    assert _weight_grams(p) == expected
