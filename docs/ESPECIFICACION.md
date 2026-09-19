# Especificación — Escáner de arbitraje retail MX → Mercado Libre

**[verificado]** = confirmado en fuente oficial o prueba en vivo (19-sep-2026); **[supuesto]** =
juicio experto calibrable; **[estimado]** = número de relleno que hay que reemplazar.

## 1. Tesis e hipótesis falsable

Hipótesis: *productos en liquidación en retailers mexicanos se consiguen por debajo del precio
competitivo de Mercado Libre por más que el costo total de revenderlos*. El costo total no es
chico: comisión (~13 % Clásica / ~17.5 % Premium **[supuesto]**), envío que paga el vendedor
desde $299, retenciones 2026 (2.5 % ISR + 8 % IVA con RFC, sobre el precio sin IVA
**[verificado]**), empaque, devoluciones y capital parado. Una brecha bruta de 30 % puede ser
cero neto.

**El giro: usarlo uno mismo antes de venderlo.** Cada oportunidad detectada abre una posición
de papel y se marca a mercado durante días. Regla de decisión preregistrada (antes de ver datos):

| Veredicto | Condición (≥ 30 posiciones marcadas en ≥ 10 días distintos) |
|---|---|
| **GO a capital real (pequeño)** | IC90 % de la utilidad por posición > 0 **y** vida mediana de la oferta > 6 h **y** oportunidades/semana × utilidad media ≥ meta mensual |
| **KILL** | IC90 % de la utilidad por posición < 0 |
| **Seguir midiendo** | IC cruza cero o no hay muestra |

Riesgos que el papel NO mide: que la compra real se cancele, stock limitado por cliente, tiempo
de venta real (el papel supone que vendes al precio competitivo), devoluciones y fraude. Por
eso el GO es "capital pequeño", no escala.

## 2. Restricciones verificadas

**Mercado Libre (prueba en vivo sin token, 19-sep-2026)**

| Endpoint | Sin token |
|---|---|
| `/sites/MLM/search`, `/products/search` (q o `product_identifier`), `/products/{id}`, `/products/{id}/items`, `/highlights/…`, `/trends/MLM`, `/sites/MLM/listing_prices` | **403/401 — requiere OAuth de usuario** |
| `/sites/MLM/domain_discovery/search?q=` (predictor de categoría), `/categories/{id}/attributes` | **público** |

→ El escáner necesita que el operador autorice su propia cuenta de ML (flujo authorization code,
refresh token de un solo uso que rota; `expires_in` ≈ 10800 s). Sin token solo hay modo
demo/offline.

Atributos útiles del catálogo: `GTIN`, `BRAND`, `MODEL`, `SELLER_SKU`. `sold_quantity` pasó a ser
"referencial" (rangos) desde abr-2025: la demanda se estima como señal débil.

**Comisión y envío**: `listing_prices` (params `price, category_id, listing_type_id,
logistic_type, shipping_mode, billable_weight`) y `/users/{id}/shipping_options/free` (params
`dimensions, item_price, listing_type_id, mode, condition, logistic_type, verbose`). Envío gratis
obligatorio desde $299 MXN; cargo fijo bajo $299 solo en Flex/ME1. Tabla de envío: solo el tramo
≤ 0.3 kg está verificado ($104.8 lista; verde −50 %, amarilla −40 %).

**Impuestos 2026** (LIF 2026, DOF 07-11-2025): ISR 2.5 % con RFC / 20 % sin RFC; IVA 8 % con RFC
/ 16 % sin RFC; base = precio sin IVA; sin mínimo exento. Opción de pago definitivo con ingresos
≤ $300,000/año.

## 3. Fuentes: qué se permite y qué no

Principio: **nunca evadir detección de bots** (CAPTCHA, WAF, rotación de IPs, headless con
huellas falsas). Si un sitio bloquea, esa fuente no existe para este programa.

| Fuente | Estado | Uso en el escáner |
|---|---|---|
| **Promodescuentos RSS** `https://www.promodescuentos.com/rss` | RSS 2.0 activo; campos `title, category, pepper:merchant{name, price}, link, pubDate, description, media, guid` | Fuente principal del MVP (uso personal). Su robots.txt permite `/` a agentes genéricos y bloquea crawlers de IA; el escáner se identifica con UA propio y honesto. Sus términos prohíben reproducir en otros sitios sin autorización → **para vender el producto como SaaS hace falta licencia**. |
| **Keepa API** (Amazon MX, `domain=11`) | Oficial, de pago (~€49/mes plan inicial); `deal` 5 tokens/consulta, `product` 1 token/ASIN con historial | Fuente Amazon MX con llave del operador. |
| Amazon PA-API 5 | **Descontinuada** (403 desde may-2026); Creators API exige Afiliados + 10 ventas/30 días | No se usa. |
| Walmart MX | CAPTCHA PerimeterX | **No se toca.** |
| Liverpool | WAF 403 en todo el sitio | **No se toca.** |
| Coppel | timeouts sistemáticos | **No se toca.** |
| Bodega Aurrera, Home Depot MX (Crawl-delay 7 s), Costco MX | sin bloqueo observado | Solo página de producto individual enlazada desde una oferta (JSON-LD `Product`), respetando robots.txt y crawl-delay, ≤ 1 petición por host cada N s, alto total ante 403/429/CAPTCHA. |
| CSV/JSON manual | — | Importar ofertas de cualquier fuente sobre la que el operador tenga derecho. |

## 4. Núcleo ya implementado (`src/escaner/`)

- `economics/taxes.py`: perfiles `sin_rfc`, `plataformas`, `resico`, `empresarial`.
- `economics/fees.py`: modelos de tabla + parsers de las respuestas de la API.
- `economics/profit.py`: utilidad neta con desglose, precio de equilibrio (barrido, la función
  salta en $299), precio máximo de compra para un ROI objetivo.
- `matching/normalize.py` + `matching/score.py`: rasgos (GTIN con dígito verificador, códigos de
  modelo, modelos cortos, capacidades, paquete, variante, generación, accesorio, condición, marca)
  y puntuación asimétrica: vetos duros → GTIN igual → logística calibrable. Bandas
  match ≥ 0.90 / revisar ≥ 0.60.
- `paper/stats.py`: Kaplan–Meier de vida de la oferta (con censura), bootstrap por bloques de
  día, veredicto preregistrado.

## 5. Precio de venta realista

No el más caro de ML: `precio_venta = min(buy_box, p25 de publicaciones nuevas comparables con
envío gratis) × (1 − undercut)` con undercut 2 % por defecto. Si hay < 3 comparables, la
oportunidad se marca `liquidez_baja`.
