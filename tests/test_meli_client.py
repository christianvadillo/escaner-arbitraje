"""MLClient sobre httpx.MockTransport: search, ruta GTIN (products_search + product_items),
listing_prices, multiget de items en bloques de 20, y reintentos SOLO en 429/5xx (nunca en 404)."""

from __future__ import annotations

import httpx
import pytest

from escaner.economics.fees import FeeQuote
from escaner.meli.client import API_BASE, MLApiError, MLClient, listing_from_item


def _item(item_id: str, title: str, price: float, **extra) -> dict:
    return {
        "id": item_id,
        "title": title,
        "price": price,
        "condition": "new",
        "listing_type_id": "gold_special",
        "category_id": "MLM1000",
        "shipping": {"free_shipping": True},
        "attributes": [{"id": "BRAND", "value_name": "Samsung"}, {"id": "GTIN", "value_name": "7501055363056"}],
        **extra,
    }


def test_listing_from_item_reads_attribute_list():
    listing = listing_from_item(_item("MLM1", "Galaxy A15", 4299.0))
    assert listing.brand == "Samsung"
    assert listing.gtin == "7501055363056"
    assert listing.free_shipping is True


def test_search_hits_the_right_endpoint_and_parses_results():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sites/MLM/search"
        assert request.url.params["q"] == "galaxy a15"
        return httpx.Response(200, json={"results": [_item("MLM1", "Galaxy A15", 4299.0)]})

    client = MLClient(http=httpx.Client(base_url=API_BASE, transport=httpx.MockTransport(handler)))
    results = client.search("galaxy a15", limit=10)
    assert len(results) == 1
    assert results[0].item_id == "MLM1"


def test_gtin_path_products_search_then_product_items():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/products/search":
            assert request.url.params["product_identifier"] == "7501055363056"
            return httpx.Response(200, json={"results": [{"id": "MLM-P-A15"}]})
        if request.url.path == "/products/MLM-P-A15/items":
            return httpx.Response(200, json={"results": [_item("MLM1", "Galaxy A15", 4299.0)]})
        raise AssertionError(request.url.path)

    client = MLClient(http=httpx.Client(base_url=API_BASE, transport=httpx.MockTransport(handler)))
    products = client.products_search(product_identifier="7501055363056")
    assert products == [{"id": "MLM-P-A15"}]
    items = client.product_items("MLM-P-A15")
    assert len(items) == 1 and items[0].item_id == "MLM1"
    assert calls == ["/products/search", "/products/MLM-P-A15/items"]


def test_items_multiget_splits_into_chunks_of_20():
    requested_id_counts = []

    def handler(request: httpx.Request) -> httpx.Response:
        ids = request.url.params["ids"].split(",")
        requested_id_counts.append(len(ids))
        return httpx.Response(200, json=[{"code": 200, "body": _item(i, f"Item {i}", 100.0)} for i in ids])

    client = MLClient(http=httpx.Client(base_url=API_BASE, transport=httpx.MockTransport(handler)))
    ids = [f"MLM{i}" for i in range(45)]
    out = client.items(ids)
    assert len(out) == 45
    assert requested_id_counts == [20, 20, 5]


def test_listing_prices_parses_fee_quote():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sites/MLM/listing_prices"
        assert request.url.params["price"] == "1000.0"
        return httpx.Response(
            200,
            json=[{"sale_fee_details": {"percentage_fee": 13.0, "fixed_fee": 0, "financing_add_on_fee": 0}}],
        )

    client = MLClient(http=httpx.Client(base_url=API_BASE, transport=httpx.MockTransport(handler)))
    fq = client.listing_prices(1000.0)
    assert isinstance(fq, FeeQuote)
    assert fq.percentage == pytest.approx(0.13)
    assert fq.source == "api"


def test_retries_on_5xx_but_not_on_404():
    attempts = {"n": 0}

    def flaky_handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"results": []})

    client = MLClient(
        http=httpx.Client(base_url=API_BASE, transport=httpx.MockTransport(flaky_handler)),
        sleep=lambda _s: None,
    )
    client.search("algo")
    assert attempts["n"] == 3  # 2 fallos 503 + 1 éxito, todo reintentado

    def not_found_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    client2 = MLClient(http=httpx.Client(base_url=API_BASE, transport=httpx.MockTransport(not_found_handler)))
    with pytest.raises(MLApiError) as exc_info:
        client2.product("MLM-NOPE")
    assert exc_info.value.status_code == 404


def test_access_token_sets_bearer_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer TOK123"
        return httpx.Response(200, json={"id": 1})

    client = MLClient(
        http=httpx.Client(base_url=API_BASE, transport=httpx.MockTransport(handler)), access_token="TOK123"
    )
    client.me()
