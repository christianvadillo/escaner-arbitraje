"""Keepa: centavos → MXN, -1 = sin oferta, tokensLeft, `KeepaSource.fetch`/`current_price`
sobre un `httpx.MockTransport` (nunca toca la red real de Keepa)."""

from __future__ import annotations

import httpx
import pytest

from escaner.sources.keepa import (
    BASE_URL,
    KeepaDealFilter,
    KeepaSource,
    cents_to_mxn,
    parse_deal_response,
    parse_product_response,
)

DEAL_FIXTURE = {
    "tokensLeft": 173,
    "deals": [
        {"asin": "B0ABC12345", "title": "Bocina Bluetooth Liquidación", "current": [34900, -1, 39900]},
        {"asin": "B0SINPRECIO", "title": "Sin oferta actual", "current": [-1, -1, -1]},
        {"asin": None, "title": "Sin ASIN", "current": [1000]},
    ],
}

PRODUCT_FIXTURE = {
    "tokensLeft": 172,
    "products": [
        {
            "asin": "B0ABC12345",
            "title": "Bocina Bluetooth Liquidación",
            "eanList": ["0194253401698"],
            "brand": "Acme",
            "csv": [[26611200000, 34900, 26611201000, 32900]],
        }
    ],
}


def test_cents_to_mxn_handles_sentinel():
    assert cents_to_mxn(34900) == 349.00
    assert cents_to_mxn(-1) is None
    assert cents_to_mxn(None) is None


def test_parse_deal_response_skips_no_offer_and_missing_asin():
    offers, tokens_left = parse_deal_response(DEAL_FIXTURE)
    assert tokens_left == 173
    assert len(offers) == 1
    o = offers[0]
    assert o.source == "keepa"
    assert o.price == 349.00
    assert o.guid == "B0ABC12345"
    assert o.url == "https://www.amazon.com.mx/dp/B0ABC12345"


def test_parse_product_response_uses_last_csv_price_and_ean():
    offers, tokens_left = parse_product_response(PRODUCT_FIXTURE)
    assert tokens_left == 172
    assert len(offers) == 1
    assert offers[0].price == 329.00  # último valor de la serie csv[0], no el primero
    assert offers[0].gtin == "0194253401698"
    assert offers[0].brand == "Acme"


def test_deal_filter_selection_uses_cents_and_price_type_zero():
    f = KeepaDealFilter(price_min_mxn=100, price_max_mxn=500, min_drop_percent=25)
    sel = f.to_selection()
    assert sel["priceRange"] == {"min": 10000, "max": 50000}
    assert sel["priceTypes"] == [0]
    assert sel["deltaPercentRange"] == {"min": 25, "max": 100}


def test_keepa_source_fetch_and_current_price_over_mock_transport():
    seen_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        assert request.url.params["key"] == "fake-key"
        if request.url.path == "/deal":
            return httpx.Response(200, json=DEAL_FIXTURE)
        if request.url.path == "/product":
            return httpx.Response(200, json=PRODUCT_FIXTURE)
        raise AssertionError(f"ruta inesperada {request.url.path}")

    http_client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    source = KeepaSource("fake-key", http_client)

    offers = source.fetch(limit=10)
    assert len(offers) == 1
    assert source.tokens_left == 173

    price = source.current_price("B0ABC12345")
    assert price == pytest.approx(329.00)
    assert source.tokens_left == 172
    assert seen_paths == ["/deal", "/product"]
