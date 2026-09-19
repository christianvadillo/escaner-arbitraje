"""fake.py — emulación offline de la API de Mercado Libre sobre `httpx.MockTransport`.

`escaner demo` y los tests corren sobre esto: mismos endpoints y formas de respuesta que
`meli.client.MLClient` espera, servidos desde los fixtures (`escaner.sources.fixtures`) en vez
de la red. `candidates.py`, `pricing.py` y `paper/ledger.py` no saben la diferencia — reciben un
`MLClient` con este transporte inyectado.

La búsqueda por texto (`/sites/MLM/search` y `/products/search?q=`) es una coincidencia de
tokens sobre el título normalizado (reusa `matching.normalize`), no un motor de relevancia real:
alcanza para que las pruebas de emparejamiento sean significativas sin fingir un ranking que no
podemos verificar.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from escaner.matching.normalize import normalize_title

_ATTR_RE = re.compile(r"^/categories/[^/]+/attributes$")
_SHIPPING_FREE_RE = re.compile(r"^/users/[^/]+/shipping_options/free$")
_PRODUCT_ITEMS_RE = re.compile(r"^/products/([^/]+)/items$")
_PRODUCT_RE = re.compile(r"^/products/([^/]+)$")


class FakeMeLiBackend:
    """Índice en memoria de items/productos fake, construido desde los fixtures."""

    def __init__(self, items: list[dict[str, Any]], products: list[dict[str, Any]]) -> None:
        self.items: dict[str, dict[str, Any]] = {str(it["id"]): it for it in items}
        self.products: dict[str, dict[str, Any]] = {str(p["id"]): p for p in products}
        self.me: dict[str, Any] = {
            "id": 999888777,
            "nickname": "OPERADOR_DEMO",
            "seller_reputation": {"level_id": "5_green"},
        }

    def search_items(self, q: str, limit: int, condition: str | None = None) -> list[dict[str, Any]]:
        qtoks = {t for t in normalize_title(q).split() if len(t) > 1}
        if not qtoks:
            return []
        scored: list[tuple[int, dict[str, Any]]] = []
        for it in self.items.values():
            if condition and it.get("condition", "new") != condition:
                continue
            ttoks = set(normalize_title(it["title"]).split())
            overlap = len(qtoks & ttoks)
            if overlap:
                scored.append((overlap, it))
        scored.sort(key=lambda pair: -pair[0])
        return [it for _, it in scored[:limit]]

    def items_for_product(self, product_id: str) -> list[dict[str, Any]]:
        return [it for it in self.items.values() if it.get("catalog_product_id") == product_id]

    def products_for_gtin(self, gtin: str) -> list[dict[str, Any]]:
        pids = {
            it.get("catalog_product_id")
            for it in self.items.values()
            if it.get("catalog_product_id") and _item_gtin(it) == gtin
        }
        return [self.products[p] for p in pids if p in self.products]


def _item_gtin(item: dict[str, Any]) -> str | None:
    for a in item.get("attributes") or []:
        if a.get("id") == "GTIN":
            return a.get("value_name")
    return None


def _fake_listing_price(params: dict[str, str]) -> dict[str, Any]:
    price = float(params.get("price", 0) or 0)
    listing_type = params.get("listing_type_id", "gold_special")
    pct = 13.0 if listing_type == "gold_special" else 17.5
    return {
        "listing_type_id": listing_type,
        "sale_fee_amount": round(price * pct / 100, 2),
        "sale_fee_details": {"percentage_fee": pct, "fixed_fee": 0, "financing_add_on_fee": 0},
    }


def _fake_shipping_free(params: dict[str, str]) -> dict[str, Any]:
    return {"coverage": {"all_country": {"list_cost": 124.0}, "discount": {"rate": 0.5}}}


def make_transport(backend: FakeMeLiBackend) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = {k: v for k, v in request.url.params.multi_items()}

        if path == "/users/me":
            return httpx.Response(200, json=backend.me)

        if path == "/sites/MLM/search":
            limit = int(params.get("limit", 50))
            results = backend.search_items(params.get("q", ""), limit, params.get("condition"))
            return httpx.Response(200, json={"results": results, "paging": {"total": len(results)}})

        if path == "/products/search":
            pid = params.get("product_identifier")
            if pid:
                results = backend.products_for_gtin(pid)
            else:
                found = backend.search_items(params.get("q", ""), 20)
                pids = {it.get("catalog_product_id") for it in found if it.get("catalog_product_id")}
                results = [backend.products[p] for p in pids if p in backend.products]
            return httpx.Response(200, json={"results": results})

        m = _PRODUCT_ITEMS_RE.match(path)
        if m:
            return httpx.Response(200, json={"results": backend.items_for_product(m.group(1))})

        m = _PRODUCT_RE.match(path)
        if m:
            product = backend.products.get(m.group(1))
            if not product:
                return httpx.Response(404, json={"message": "product not found"})
            return httpx.Response(200, json=product)

        if path == "/items":
            ids = [i for i in params.get("ids", "").split(",") if i]
            body = [{"code": 200, "body": backend.items[i]} for i in ids if i in backend.items]
            return httpx.Response(200, json=body)

        if path == "/sites/MLM/listing_prices":
            return httpx.Response(200, json=[_fake_listing_price(params)])

        if _SHIPPING_FREE_RE.match(path):
            return httpx.Response(200, json=_fake_shipping_free(params))

        if path == "/sites/MLM/domain_discovery/search":
            return httpx.Response(
                200,
                json=[
                    {
                        "category_id": "MLM1000",
                        "category_name": "Electrónica, Audio y Video",
                        "domain_id": "MLM-CELLPHONES",
                    }
                ],
            )

        if _ATTR_RE.match(path):
            return httpx.Response(
                200,
                json=[
                    {"id": "BRAND", "name": "Marca"},
                    {"id": "MODEL", "name": "Modelo"},
                    {"id": "GTIN", "name": "Código universal de producto"},
                ],
            )

        return httpx.Response(404, json={"message": f"ruta fake no implementada: {path}"})

    return httpx.MockTransport(handler)


def build_fake_http_client(backend: FakeMeLiBackend) -> httpx.Client:
    from escaner.meli.client import API_BASE

    return httpx.Client(base_url=API_BASE, transport=make_transport(backend))
