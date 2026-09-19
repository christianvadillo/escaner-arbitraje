"""fixtures.py — datos offline para `escaner demo` y los tests E2E: ofertas de retailer +
catálogo de Mercado Libre correspondiente, servidos por `meli.fake` sin tocar la red.

15 ofertas: 11 productos "de verdad" (cada uno con su catálogo de ML correspondiente, sin ningún
veto de por medio) y 4 señuelos que `matching.score.veto` debe rechazar. De los 11, la mayoría
(pero deliberadamente NO todos — ver `test_scanner_e2e.py`) sobrevive comisión + envío +
impuestos + reserva de devoluciones + costo de capital por encima de `min_roi`/`min_profit`: el
punto de `escaner demo` es mostrar el filtro de umbrales filtrando algo de verdad, no una lista
donde "todo pasa" porque los números se escogieron para que pasaran.

  - una **funda** (accesorio) para el mismo teléfono de la oportunidad #1,
  - un iPhone 15 **reacondicionado** contra el catálogo de iPhone 15 nuevo de la oportunidad #2,
  - un **paquete de 2** termos contra el catálogo de un termo suelto de la oportunidad #7,
  - unos Galaxy Buds 2 (no-Pro) contra un catálogo que solo tiene la variante **Pro**.

Los señuelos reutilizan intencionalmente el catálogo de ML de una oportunidad real: así el
escáner de verdad tiene que ENCONTRAR el candidato parecido (por marca/modelo) y vetarlo, no
simplemente no encontrar nada que buscar.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from escaner.models import Offer

SOURCE_NAME = "fixtures"


# ── construcción de items/productos de ML (forma cruda, la que parsea meli.client) ─────────


def _item(
    item_id: str,
    title: str,
    price: float,
    *,
    brand: str | None = None,
    gtin: str | None = None,
    model: str | None = None,
    condition: str = "new",
    free_shipping: bool = True,
    catalog_product_id: str | None = None,
    category_id: str = "MLM1000",
    sold_quantity: int | None = None,
    weight_attr: str | None = None,
    seller_id: str = "111111",
) -> dict[str, Any]:
    attrs: list[dict[str, str]] = []
    if brand:
        attrs.append({"id": "BRAND", "value_name": brand})
    if gtin:
        attrs.append({"id": "GTIN", "value_name": gtin})
    if model:
        attrs.append({"id": "MODEL", "value_name": model})
    if weight_attr:
        attrs.append({"id": "WEIGHT", "value_name": weight_attr})
    return {
        "id": item_id,
        "title": title,
        "price": price,
        "condition": condition,
        "listing_type_id": "gold_special",
        "category_id": category_id,
        "catalog_product_id": catalog_product_id,
        "shipping": {"free_shipping": free_shipping},
        "attributes": attrs,
        "sold_quantity": sold_quantity,
        "permalink": f"https://articulo.mercadolibre.com.mx/{item_id}",
        "seller_id": seller_id,
    }


def _product(product_id: str, buy_box_price: float) -> dict[str, Any]:
    return {"id": product_id, "status": "active", "buy_box_winner": {"price": buy_box_price}}


GALAXY_A15_GTIN = "7501055363056"


def ml_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    # 1. Samsung Galaxy A15 — vía GTIN (ejercita el camino products_search+product_items).
    for i, (color, price) in enumerate([("Azul Oscuro", 4299), ("Negro", 4399), ("Verde", 4499), ("Lila", 4599)]):
        items.append(
            _item(
                f"MLM-A15-{i}",
                f"Celular Samsung Galaxy A15 128 GB {color} Dual SIM",
                price,
                brand="Samsung",
                gtin=GALAXY_A15_GTIN,
                model="SM-A155M",
                catalog_product_id="MLM-P-A15",
                sold_quantity=50 + i * 10,
            )
        )

    # 2. Apple iPhone 15 128GB Negro — nuevo únicamente (el señuelo D2 es reacondicionado).
    for i, price in enumerate([18999, 19499, 19999, 20499]):
        items.append(
            _item(f"MLM-IP15-{i}", "Apple iPhone 15 128 GB Negro", price, brand="Apple", sold_quantity=30 + i * 5)
        )

    # 3. Licuadora Oster BLSTPYG1210.
    for i, price in enumerate([1649, 1699, 1749, 1799]):
        items.append(
            _item(
                f"MLM-OST-{i}",
                "Licuadora Oster Xpert Series 1.25l Blstpyg1210 Plata",
                price,
                brand="Oster",
                model="BLSTPYG1210",
            )
        )

    # 4. Smart TV Hisense 55A6K (pesada: trae WEIGHT explícito para probar la resolución por
    #    atributos de ML cuando la oferta no trae peso propio).
    for i, price in enumerate([8999, 9299, 9599, 9899]):
        items.append(
            _item(
                f"MLM-HIS-{i}",
                "Smart TV Hisense 55A6K 55 Pulgadas 4K UHD Google TV",
                price,
                brand="Hisense",
                model="55A6K",
                weight_attr="15 kg" if i == 0 else None,
            )
        )

    # 5. Xbox Series X 1TB.
    for i, price in enumerate([11999, 12499, 12999]):
        items.append(
            _item(
                f"MLM-XBX-{i}", "Consola Microsoft Xbox Series X 1 TB Negro", price, brand="Microsoft", model="Series X"
            )
        )

    # 6. Sony WH-1000XM5.
    for i, price in enumerate([4699, 4899, 4999, 5199]):
        items.append(
            _item(
                f"MLM-SNY-{i}",
                "Sony Wh-1000xm5 Audífonos Inalámbricos Cancelación De Ruido",
                price,
                brand="Sony",
                model="WH-1000XM5",
            )
        )

    # 7. Termo Stanley 1.4L Verde (suelto: el señuelo D3 es un paquete de 2).
    for i, price in enumerate([649, 699, 749]):
        items.append(_item(f"MLM-STN-{i}", "Termo Stanley Clásico 1.4 L Verde Botella", price, brand="Stanley"))

    # 8. LEGO Star Wars 75301.
    for i, price in enumerate([1399, 1499, 1599]):
        items.append(
            _item(
                f"MLM-LEGO-{i}", "LEGO Star Wars Caza Ala-X de Luke Skywalker 75301", price, brand="Lego", model="75301"
            )
        )

    # 9. Nintendo Switch OLED Blanco.
    for i, price in enumerate([7299, 7499, 7699, 7899]):
        items.append(_item(f"MLM-NSW-{i}", "Consola Nintendo Switch Oled 64gb Blanca", price, brand="Nintendo"))

    # 10. PlayStation 5 Slim (NO Digital: evita chocar con el veto de variante).
    for i, price in enumerate([12999, 13499, 13999]):
        items.append(_item(f"MLM-PS5-{i}", "Consola PlayStation 5 Slim 1TB", price, brand="Sony"))

    # 11. Aspiradora Bissell PowerForce Helix.
    for i, price in enumerate([1899, 1999, 2099]):
        items.append(_item(f"MLM-BIS-{i}", "Aspiradora Bissell Powerforce Helix Bagless", price, brand="Bissell"))

    # D4. Solo la variante Pro está en catálogo — el señuelo (no-Pro) debe vetarse.
    for i, price in enumerate([1899, 1999, 2099]):
        items.append(_item(f"MLM-BUDS-{i}", "Samsung Galaxy Buds 2 Pro Audífonos", price, brand="Samsung"))

    return items


def ml_products() -> list[dict[str, Any]]:
    return [_product("MLM-P-A15", 4199.0)]


def ml_catalog() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return ml_items(), ml_products()


# ── ofertas de retailer ──────────────────────────────────────────────────────────────────


def _hours_ago(h: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=h)


def offers() -> list[Offer]:
    """Función (no lista estática): `published_at` se calcula relativo a "ahora" en cada
    llamada, para que la curva de Kaplan–Meier del demo tenga variedad sin importar cuándo se
    corra.

    Precios de compra REALISTAS: liquidaciones 20–37 % bajo el precio competitivo de ML. Con
    los costos reales (comisión, retenciones 2026, envío, devoluciones, capital) un descuento de
    ~20–25 % queda en cero o en pérdida; solo los de ≥ 30–35 % pasan el ROI mínimo. La demo debe
    enseñar eso — que la mayoría de las "ofertas" NO son negocio — y no lo contrario."""
    legit = [
        Offer(
            source=SOURCE_NAME,
            merchant="Bodega Aurrera",
            title="Samsung Galaxy A15 128GB Azul Oscuro Dual SIM",
            price=3199,
            url="https://www.bodegaaurrera.com.mx/liquidacion/galaxy-a15",
            guid="fx-galaxy-a15",
            published_at=_hours_ago(6),
            gtin=GALAXY_A15_GTIN,
            brand="Samsung",
            model="SM-A155M",
            weight_g=250,
            category="celulares",
        ),
        Offer(
            source=SOURCE_NAME,
            merchant="Costco MX",
            title="Apple iPhone 15 128GB Negro",
            price=14999,
            url="https://www.costco.com.mx/liquidacion/iphone-15",
            guid="fx-iphone-15",
            published_at=_hours_ago(20),
            brand="Apple",
            weight_g=200,
            category="celulares",
        ),
        Offer(
            source=SOURCE_NAME,
            merchant="Costco MX",
            title="Licuadora Oster BLSTPYG1210 600W 1.25 Litros",
            price=999,
            url="https://www.costco.com.mx/liquidacion/oster-blstpyg1210",
            guid="fx-oster-blender",
            published_at=_hours_ago(3),
            brand="Oster",
            model="BLSTPYG1210",
            weight_g=1800,
            category="cocina",
        ),
        Offer(
            source=SOURCE_NAME,
            merchant="Home Depot MX",
            title="Smart TV Hisense 55A6K 55 Pulgadas 4K UHD Google TV",
            price=6499,
            url="https://www.homedepot.com.mx/liquidacion/hisense-55a6k",
            guid="fx-hisense-55a6k",
            published_at=_hours_ago(130),
            brand="Hisense",
            model="55A6K",
            category="pantallas",
        ),
        Offer(
            source=SOURCE_NAME,
            merchant="Bodega Aurrera",
            title="Consola Microsoft Xbox Series X 1TB Negro",
            price=9499,
            url="https://www.bodegaaurrera.com.mx/liquidacion/xbox-series-x",
            guid="fx-xbox-series-x",
            published_at=_hours_ago(400),
            brand="Microsoft",
            model="Series X",
            weight_g=4450,
            category="consolas",
        ),
        Offer(
            source=SOURCE_NAME,
            merchant="Costco MX",
            title="Audífonos Sony WH-1000XM5 Negro",
            price=3499,
            url="https://www.costco.com.mx/liquidacion/sony-wh1000xm5",
            guid="fx-sony-wh1000xm5",
            published_at=_hours_ago(48),
            brand="Sony",
            model="WH-1000XM5",
            weight_g=250,
            category="audio",
        ),
        Offer(
            source=SOURCE_NAME,
            merchant="Home Depot MX",
            title="Termo Stanley Clásico 1.4 L Verde",
            price=449,
            url="https://www.homedepot.com.mx/liquidacion/stanley-termo-1-4l",
            guid="fx-stanley-termo",
            published_at=_hours_ago(15),
            brand="Stanley",
            weight_g=900,
            category="hogar",
        ),
        Offer(
            source=SOURCE_NAME,
            merchant="Bodega Aurrera",
            title="Lego Star Wars 75301 Ala-X de Luke",
            price=799,
            url="https://www.bodegaaurrera.com.mx/liquidacion/lego-75301",
            guid="fx-lego-75301",
            published_at=_hours_ago(90),
            brand="Lego",
            model="75301",
            weight_g=700,
            category="juguetes",
        ),
        Offer(
            source=SOURCE_NAME,
            merchant="Costco MX",
            title="Nintendo Switch OLED Blanco",
            price=5999,
            url="https://www.costco.com.mx/liquidacion/switch-oled",
            guid="fx-switch-oled",
            published_at=_hours_ago(10),
            brand="Nintendo",
            weight_g=420,
            category="consolas",
        ),
        Offer(
            source=SOURCE_NAME,
            merchant="Home Depot MX",
            title="Consola PlayStation 5 Slim",
            price=9999,
            url="https://www.homedepot.com.mx/liquidacion/ps5-slim",
            guid="fx-ps5-slim",
            published_at=_hours_ago(600),
            # Sin `brand` a propósito: "PlayStation" no aparece literal en el título y la
            # búsqueda fake es por texto exacto (a diferencia del buscador real de ML, que sí
            # es brand-aware). Sin esto, una query "Sony" encuentra los audífonos, no la
            # consola. `features()` igual infiere marca "sony" de "playstation" vía BRAND_CANON
            # al puntuar, así que el matching en sí no pierde nada.
            weight_g=3200,
            category="consolas",
        ),
        Offer(
            source=SOURCE_NAME,
            merchant="Bodega Aurrera",
            title="Aspiradora Bissell PowerForce Helix",
            price=1199,
            url="https://www.bodegaaurrera.com.mx/liquidacion/bissell-powerforce",
            guid="fx-bissell-powerforce",
            published_at=_hours_ago(70),
            brand="Bissell",
            weight_g=3800,
            category="hogar",
        ),
    ]
    decoys = [
        # D1: funda (accesorio) — debe encontrar el Galaxy A15 real y vetarlo.
        Offer(
            source=SOURCE_NAME,
            merchant="Bodega Aurrera",
            title="Funda Silicona para Samsung Galaxy A15",
            price=129,
            url="https://www.bodegaaurrera.com.mx/liquidacion/funda-galaxy-a15",
            guid="fx-decoy-funda",
            published_at=_hours_ago(5),
            brand="Samsung",
            category="accesorios",
        ),
        # D2: reacondicionado — debe encontrar el iPhone 15 nuevo y vetarlo por condición.
        Offer(
            source=SOURCE_NAME,
            merchant="Costco MX",
            title="Apple iPhone 15 128GB Negro Reacondicionado",
            price=13500,
            url="https://www.costco.com.mx/liquidacion/iphone-15-reacondicionado",
            guid="fx-decoy-iphone-refurb",
            published_at=_hours_ago(22),
            brand="Apple",
            category="celulares",
        ),
        # D3: paquete de 2 — debe encontrar el termo suelto y vetarlo por cantidad de paquete.
        Offer(
            source=SOURCE_NAME,
            merchant="Home Depot MX",
            title="Paquete de 2 Termos Stanley 1.4 L Clásico Verde",
            price=599,
            url="https://www.homedepot.com.mx/liquidacion/stanley-termo-pack2",
            guid="fx-decoy-termo-pack",
            published_at=_hours_ago(16),
            brand="Stanley",
            category="hogar",
        ),
        # D4: no-Pro contra un catálogo que solo tiene la variante Pro — veto de variante.
        Offer(
            source=SOURCE_NAME,
            merchant="Bodega Aurrera",
            title="Samsung Galaxy Buds 2 Audífonos",
            price=899,
            url="https://www.bodegaaurrera.com.mx/liquidacion/galaxy-buds-2",
            guid="fx-decoy-buds-nonpro",
            published_at=_hours_ago(12),
            brand="Samsung",
            category="audio",
        ),
    ]
    return legit + decoys


DECOY_GUIDS = frozenset({"fx-decoy-funda", "fx-decoy-iphone-refurb", "fx-decoy-termo-pack", "fx-decoy-buds-nonpro"})


class FixturesSource:
    name = SOURCE_NAME

    def fetch(self, limit: int = 50) -> list[Offer]:
        return offers()[:limit]
