"""jsonld.py — enriquece una `Offer` con datos de SU PROPIA página de producto, leyendo el
`Product` de schema.org que casi todo e-commerce serio incrusta como `<script
type="application/ld+json">`. Con regex/`html.parser`, no un DOM completo (regla del proyecto:
stdlib, nada de BeautifulSoup/lxml) — ld+json es JSON dentro de una etiqueta, no HTML que haya
que parsear de verdad.

SOLO se visita si el host de `offer.url` está en `allowed_product_hosts` (ESPECIFICACION §3:
Bodega Aurrera, Home Depot MX, Costco MX — los que no bloquean). Esto NO es una fuente de
descubrimiento: nunca busca nada, solo relee una URL que una fuente ya trajo.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import replace
from urllib.parse import urlparse

from escaner.http import PoliteClient
from escaner.models import Offer

_LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL
)
_WEIGHT_STR_RE = re.compile(r"([\d.]+)\s*(kg|kgm|g|gr)\b")


def _iter_candidates(data: object) -> Iterator[dict]:
    if isinstance(data, list):
        for d in data:
            yield from _iter_candidates(d)
    elif isinstance(data, dict):
        graph = data.get("@graph")
        if isinstance(graph, list):
            for d in graph:
                yield from _iter_candidates(d)
        else:
            yield data


def _is_product(node: dict) -> bool:
    t = node.get("@type")
    return "Product" in t if isinstance(t, list) else t == "Product"


def extract_product_jsonld(html: str) -> dict | None:
    """Primer bloque ld+json de tipo `Product` (soporta `@graph` y listas de nodos)."""
    for m in _LD_JSON_RE.finditer(html):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for candidate in _iter_candidates(data):
            if _is_product(candidate):
                return candidate
    return None


def _grams(value: float, unit: str) -> int:
    return round(value * 1000) if unit in ("kg", "kgm") else round(value)


def _weight_grams(node: dict) -> int | None:
    w = node.get("weight")
    if isinstance(w, dict):
        try:
            value = float(w.get("value"))
        except (TypeError, ValueError):
            return None
        unit = str(w.get("unitCode") or w.get("unitText") or "g").lower()
        return _grams(value, unit)
    if isinstance(w, str):
        m = _WEIGHT_STR_RE.search(w.lower())
        if m:
            return _grams(float(m.group(1)), m.group(2))
    return None


def _first_gtin(node: dict) -> str | None:
    for key in ("gtin13", "gtin", "gtin12", "gtin14", "gtin8"):
        v = node.get(key)
        if v:
            return str(v)
    return None


def _brand_name(node: dict) -> str | None:
    brand = node.get("brand")
    if isinstance(brand, dict):
        return brand.get("name")
    return brand if isinstance(brand, str) else None


def _offer_node(node: dict) -> dict:
    offers = node.get("offers")
    if isinstance(offers, list):
        return offers[0] if offers else {}
    return offers or {}


def host_allowed(url: str, allowed_hosts: frozenset[str]) -> bool:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    return host in {h.removeprefix("www.") for h in allowed_hosts}


def enrich_offer(offer: Offer, client: PoliteClient, allowed_hosts: frozenset[str]) -> Offer:
    """Devuelve una `Offer` nueva con `gtin/brand/model/weight_g` completados desde JSON-LD si
    el host está permitido y la página trae un `Product`. Si no aplica, o la extracción no
    encuentra nada, regresa la `Offer` sin tocar (nunca sobreescribe un dato que la fuente ya
    traía explícito)."""
    if not host_allowed(offer.url, allowed_hosts):
        return offer
    result = client.get(offer.url)
    product = extract_product_jsonld(result.text)
    if not product:
        return offer
    offers_node = _offer_node(product)
    return replace(
        offer,
        gtin=offer.gtin or _first_gtin(product),
        brand=offer.brand or _brand_name(product),
        model=offer.model or product.get("model") or product.get("sku") or product.get("mpn"),
        weight_g=offer.weight_g or _weight_grams(product),
        raw={**offer.raw, "jsonld_availability": offers_node.get("availability")},
    )
