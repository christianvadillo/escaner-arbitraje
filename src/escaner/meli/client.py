"""client.py — cliente de la API de Mercado Libre (sitio MLM) usado por `candidates.py`,
`paper/ledger.py` y los modelos de comisión/envío respaldados por API.

El `httpx.Client` es inyectable (real o el de `meli.fake`, que responde sobre los fixtures) para
que nada de este módulo distinga entre demo/test y producción: solo cambia el transporte.

Reintentos con backoff + jitter SOLO en 429 y 5xx — un 404 de "producto no existe" o un 400 de
parámetros mal armados no se arreglan reintentando, y merece propagarse tal cual.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from escaner.economics.fees import (
    FeeModel,
    FeeQuote,
    ShippingModel,
    ShippingQuote,
    TableFeeModel,
    TableShippingModel,
    fee_quote_from_listing_prices,
    shipping_quote_from_free_options,
)
from escaner.models import MLListing

API_BASE = "https://api.mercadolibre.com"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class MLApiError(RuntimeError):
    def __init__(self, status_code: int, payload: Any) -> None:
        super().__init__(f"ML API {status_code}: {payload}")
        self.status_code = status_code
        self.payload = payload


def _attrs_to_dict(raw: list[dict] | None) -> dict[str, str]:
    """Los atributos de ML vienen como lista `[{"id": "BRAND", "value_name": "Samsung"}, …]`,
    no como dict plano. GTIN/BRAND/MODEL/SELLER_SKU (ESPECIFICACION §2) se leen de aquí."""
    out: dict[str, str] = {}
    for a in raw or []:
        aid, val = a.get("id"), a.get("value_name")
        if aid and val is not None:
            out[str(aid)] = str(val)
    return out


def listing_from_item(raw: dict) -> MLListing:
    attrs = _attrs_to_dict(raw.get("attributes"))
    shipping = raw.get("shipping") or {}
    sold_quantity = raw.get("sold_quantity")
    seller_id = raw.get("seller_id")
    return MLListing(
        item_id=str(raw["id"]),
        title=raw.get("title", ""),
        price=float(raw.get("price") or 0.0),
        condition=raw.get("condition") or "new",
        free_shipping=bool(shipping.get("free_shipping")),
        listing_type=raw.get("listing_type_id") or "gold_special",
        category_id=raw.get("category_id"),
        catalog_product_id=raw.get("catalog_product_id"),
        brand=attrs.get("BRAND"),
        gtin=attrs.get("GTIN"),
        model=attrs.get("MODEL"),
        sold_quantity=int(sold_quantity) if isinstance(sold_quantity, int) else None,
        permalink=raw.get("permalink"),
        seller_id=str(seller_id) if seller_id is not None else None,
        raw=raw,
    )


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text


@dataclass
class MLClient:
    http: httpx.Client
    access_token: str | None = None
    max_retries: int = 3
    sleep: Callable[[float], None] = time.sleep
    rand: random.Random = field(default_factory=random.Random)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"} if self.access_token else {}

    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        attempt = 0
        while True:
            resp = self.http.request(method, path, headers=self._headers(), **kw)
            if resp.status_code < 400:
                return resp
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                self.sleep((2.0**attempt) + self.rand.uniform(0, 0.5))
                attempt += 1
                continue
            raise MLApiError(resp.status_code, _safe_json(resp))

    def me(self) -> dict:
        return self._request("GET", "/users/me").json()

    def search(self, q: str, limit: int = 50, condition: str = "new") -> list[MLListing]:
        resp = self._request("GET", "/sites/MLM/search", params={"q": q, "limit": limit, "condition": condition})
        return [listing_from_item(r) for r in resp.json().get("results", [])]

    def products_search(self, q: str | None = None, product_identifier: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"site_id": "MLM", "status": "active"}
        if product_identifier:
            params["product_identifier"] = product_identifier
        elif q:
            params["q"] = q
        resp = self._request("GET", "/products/search", params=params)
        return resp.json().get("results", [])

    def product(self, product_id: str) -> dict:
        return self._request("GET", f"/products/{product_id}").json()

    def product_items(self, product_id: str) -> list[MLListing]:
        resp = self._request("GET", f"/products/{product_id}/items")
        return [listing_from_item(r) for r in resp.json().get("results", [])]

    def items(self, ids: list[str]) -> list[MLListing]:
        """Multiget de 20 en 20 (límite real de `/items?ids=`)."""
        out: list[MLListing] = []
        for i in range(0, len(ids), 20):
            chunk = ids[i : i + 20]
            if not chunk:
                continue
            resp = self._request("GET", "/items", params={"ids": ",".join(chunk)})
            for row in resp.json():
                if row.get("code") == 200 and row.get("body"):
                    out.append(listing_from_item(row["body"]))
        return out

    def listing_prices(
        self,
        price: float,
        category_id: str | None = None,
        listing_type_id: str = "gold_special",
        logistic_type: str = "drop_off",
        shipping_mode: str = "me2",
        billable_weight: float | None = None,
    ) -> FeeQuote:
        params: dict[str, Any] = {"price": price, "listing_type_id": listing_type_id}
        if category_id:
            params["category_id"] = category_id
        if logistic_type:
            params["logistic_type"] = logistic_type
        if shipping_mode:
            params["shipping_mode"] = shipping_mode
        if billable_weight:
            params["billable_weight"] = billable_weight
        resp = self._request("GET", "/sites/MLM/listing_prices", params=params)
        return fee_quote_from_listing_prices(resp.json())

    def shipping_free_cost(
        self,
        user_id: str,
        dimensions: str | None = None,
        item_price: float | None = None,
        listing_type_id: str = "gold_special",
        mode: str = "me2",
        condition: str = "new",
        logistic_type: str = "drop_off",
        verbose: bool = False,
    ) -> ShippingQuote:
        params: dict[str, Any] = {
            "listing_type_id": listing_type_id,
            "mode": mode,
            "condition": condition,
            "logistic_type": logistic_type,
            "verbose": str(verbose).lower(),
        }
        if dimensions:
            params["dimensions"] = dimensions
        if item_price is not None:
            params["item_price"] = item_price
        resp = self._request("GET", f"/users/{user_id}/shipping_options/free", params=params)
        return shipping_quote_from_free_options(resp.json())

    def domain_discovery(self, q: str) -> list[dict]:
        """Público (no requiere token): predictor de categoría."""
        return self._request("GET", "/sites/MLM/domain_discovery/search", params={"q": q}).json()

    def category_attributes(self, category_id: str) -> list[dict]:
        """Público (no requiere token)."""
        return self._request("GET", f"/categories/{category_id}/attributes").json()


def _price_bucket(price: float, bucket: float) -> int:
    return int(round(price / bucket)) if bucket else int(price)


class ApiFeeModel:
    """`FeeModel` respaldado por `listing_prices`. Sin token, o si la API falla, cae al modelo
    de tabla (`fallback`) — nunca revienta el escaneo por un problema de red en la cotización."""

    def __init__(self, client: MLClient, fallback: FeeModel | None = None, price_bucket: float = 50.0) -> None:
        self._client = client
        self._fallback = fallback or TableFeeModel()
        self._bucket = price_bucket
        self._cache: dict[tuple, FeeQuote] = {}

    def quote(
        self,
        price: float,
        category_id: str | None = None,
        listing_type: str = "gold_special",
        logistic_type: str = "drop_off",
        shipping_mode: str = "me2",
        billable_weight_g: int | None = None,
    ) -> FeeQuote:
        if not self._client.access_token:
            return self._fallback.quote(
                price, category_id, listing_type, logistic_type, shipping_mode, billable_weight_g
            )
        key = (category_id, listing_type, logistic_type, shipping_mode, _price_bucket(price, self._bucket))
        if key in self._cache:
            return self._cache[key]
        try:
            fq = self._client.listing_prices(
                price,
                category_id=category_id,
                listing_type_id=listing_type,
                logistic_type=logistic_type,
                shipping_mode=shipping_mode,
                billable_weight=billable_weight_g,
            )
        except (MLApiError, httpx.HTTPError):
            return self._fallback.quote(
                price, category_id, listing_type, logistic_type, shipping_mode, billable_weight_g
            )
        self._cache[key] = fq
        return fq


class ApiShippingModel:
    """`ShippingModel` respaldado por `/users/{id}/shipping_options/free`. Resuelve el propio
    `user_id` del vendedor autenticado vía `/users/me` (cacheado) si no se lo dan explícito —
    así no hace falta pedirle al operador su ID numérico a mano."""

    def __init__(
        self,
        client: MLClient,
        fallback: ShippingModel | None = None,
        user_id: str | None = None,
        weight_bucket_g: int = 250,
    ) -> None:
        self._client = client
        self._fallback = fallback or TableShippingModel()
        self._user_id = user_id
        self._weight_bucket = weight_bucket_g
        self._cache: dict[tuple, ShippingQuote] = {}

    def _resolve_user_id(self) -> str | None:
        if self._user_id:
            return self._user_id
        try:
            self._user_id = str(self._client.me()["id"])
        except (MLApiError, httpx.HTTPError, KeyError):
            return None
        return self._user_id

    def seller_cost(self, price: float, weight_g: int, reputation: str = "green") -> ShippingQuote:
        if not self._client.access_token:
            return self._fallback.seller_cost(price, weight_g, reputation)
        user_id = self._resolve_user_id()
        if not user_id:
            return self._fallback.seller_cost(price, weight_g, reputation)
        bucket = int(round(weight_g / self._weight_bucket)) if self._weight_bucket else weight_g
        key = (bucket,)
        if key in self._cache:
            return self._cache[key]
        try:
            sq = self._client.shipping_free_cost(user_id, item_price=price)
        except (MLApiError, httpx.HTTPError):
            return self._fallback.seller_cost(price, weight_g, reputation)
        self._cache[key] = sq
        return sq
