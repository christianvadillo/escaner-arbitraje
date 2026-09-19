# Arquitectura

Flujo de una corrida (`escaner scan` / `escaner demo`, ambos llaman a `scanner.scan`):

```
Source.fetch()          sources/{promodescuentos,keepa,csv,fixtures}.py
      │  list[Offer]
      ▼
enrich_offer()           sources/jsonld.py   (solo si offer.url ∈ allowed_product_hosts)
      │  Offer (± gtin/brand/model/weight_g)
      ▼
store.offer_seen_within()  store.py           dedupe por (source, guid) en 3 días
      │
      ▼
find_candidates()        candidates.py        GTIN → products_search+product_items
      │                                       sin GTIN → search + multiget items
      │  CandidateResult(matches, review, buy_box_price)
      ▼
[si no hay match directo   judge.py           SOLO banda "review" (0.60-0.90), SOLO si
 y llm_enabled]                                settings.llm_enabled; nunca toca un veto
      │
      ▼
resolve_weight_g()       pricing.py           jsonld > atributos ML > default
realistic_sale_price()   pricing.py           min(buy_box, p25 nuevas+envío gratis) × (1-undercut)
      │
      ▼
compute_profit()         economics/profit.py  fee_model/shipping_model: API si hay token,
      │  ProfitBreakdown                      tabla si no (meli/client.py::ApiFeeModel/...)
      ▼
umbrales (min_roi, min_profit)                si no pasa → descartada, no se abre posición
      │  Opportunity
      ▼
store.save_opportunity() + ledger.open_position()   sqlite + PaperPosition
```

`escaner paper mark` corre `paper/ledger.py::mark_all` sobre las posiciones abiertas: re-cotiza
`comparables` (multiget de los mismos `item_id`, más un refresh del buy box del catálogo si
`listing.catalog_product_id` existe) y observa si la oferta de origen sigue viva según la fuente
(`KeepaLivenessChecker` / `JsonLdLivenessChecker` / censura si no hay forma de observar).
`escaner paper report` corre `paper/stats.py::summarize` (ya existente, sin tocar) sobre
`store.positions_for_stats()` y aplica la regla de decisión preregistrada.

## Por qué está dividido así

- **`http.py` es la única puerta a la red para páginas scrapeadas.** Blacklist, robots.txt,
  límite de tasa y detección de bloqueo viven en un lugar; `sources/promodescuentos.py` y
  `sources/jsonld.py` no reimplementan nada de eso, solo llaman `PoliteClient.get()`.
- **`meli/client.py` no sabe si habla con la API real o con `meli/fake.py`.** Ambos exponen el
  mismo `httpx.Client`; `MLClient` recibe el transporte inyectado. `candidates.py`,
  `pricing.py` y `paper/ledger.py` tampoco lo saben — así `escaner demo` y los tests ejercitan
  literalmente el mismo código que producción, no una simulación paralela.
- **`config.py` (Settings, pydantic) es la única frontera que valida algo.** `models.py` son
  dataclasses simples a propósito: viajan a sqlite y se comparan en tests sin la fricción de un
  modelo de validación; lo que sí cruza una frontera no confiable (variables de entorno, salida
  estructurada del LLM) usa pydantic.
- **`pricing.py` sabe resolver `weight_g`** (`resolve_weight_g`), no `scanner.py`, para que
  `paper/ledger.py` pueda reproducir EXACTAMENTE el mismo peso al re-marcar sin crear un import
  circular `scanner.py` ↔ `paper/ledger.py` (`scanner.py` ya importa `paper.ledger` para abrir
  posiciones).
- **`Opportunity` guarda `buy_box_price` y los `comparables` usados**, no solo el precio final:
  `paper/ledger.py::mark_all` necesita reconstruir la MISMA fórmula de precio con datos
  frescos. Sin esto, remarcar sin buy box sesgaría la utilidad marcada hacia arriba cada vez
  que el buy box era la restricción vinculante al detectar (bug real, atrapado por
  `tests/test_ledger.py::test_mark_with_unchanged_prices_keeps_profit_close_to_expected`).
- **El juez LLM (`judge.py`) nunca decide comprar.** Solo puede mover review→match (con
  `confidence ≥ 0.9`) o review→no_match; un veto de `matching/score.py` ni se le pregunta
  (`_escalate_to_judge` en `scanner.py` solo se invoca cuando `cr.matches` está vacío pero
  `cr.review` no — los vetados ni siquiera llegan a `cr.review`).
- **Todo lo que puede fallar por la red externa (bloqueo, host caído) degrada, no truena.**
  `_maybe_enrich`, `JsonLdLivenessChecker.check`, `ApiFeeModel.quote`, `ApiShippingModel.seller_cost`
  y `_refresh_buy_box` atrapan exactamente las excepciones "esperadas" de su capa
  (`SourceBlocked`/`RobotsDisallowed`/`BlacklistedHost` o `MLApiError`/`httpx.HTTPError`) y
  regresan al mejor dato disponible; un bug de verdad (`KeyError`, `TypeError`) se sigue
  propagando.

## Núcleo protegido vs. resto del repo

`economics/{taxes,fees,profit}.py`, `matching/{normalize,score}.py` y `paper/stats.py` (+ sus
tests) fueron el punto de partida y su semántica (tasas, vetos, pesos, fórmulas) no se tocó —
todo lo demás en este documento se construyó ALREDEDOR de esos módulos, nunca modificándolos.
`store.py`, `scanner.py`, `paper/ledger.py`, `candidates.py`, `pricing.py`, `judge.py`,
`config.py`, `models.py`, `http.py`, `meli/*` y `sources/*` son la capa nueva.

## Esquema de sqlite (`store.py`, `PRAGMA user_version`)

`offers` (dedupe por `source+guid`) → `matches`/`opportunities` (referencian `offer_id` +
`item_id` de `listings`) → `paper_positions` (una por oportunidad, `opportunity_id`) →
`paper_marks` (N por posición, la más reciente es la que usa `positions_for_stats`). `runs`
lleva el resumen de cada corrida. `http_cache` (`http.py::ensure_schema`) y `judge_cache`
(`judge.py::ensure_schema`) son dueñas de su propia tabla — `store.init_db` solo las invoca,
para que ambos módulos sigan siendo importables y probables sin levantar el esquema completo.

## Deviaciones respecto a la especificación original

- **`Settings` gana `ml_user_id` opcional** (no estaba en la lista original): `ApiShippingModel`
  necesita el ID numérico del vendedor autenticado para `/users/{id}/shipping_options/free`; si
  se deja vacío se resuelve solo con `GET /users/me` (cacheado), así que en la práctica el
  operador casi nunca necesita tocarlo.
- **`Opportunity` gana `buy_box_price`** — ver arriba ("por qué está dividido así").
- **El juez LLM SÍ quedó conectado a `scanner.py`** (`_escalate_to_judge`), aunque la
  especificación no lo pedía explícitamente en la sección de `scanner.py`: dejar `judge.py`
  completo pero sin invocar habría hecho que `llm_enabled`/`llm_model`/`llm_effort` en
  `Settings` fueran configuración muerta.
- **`meli/fake.py` busca por coincidencia de tokens normalizados**, no por un índice de queries
  exactas: así cualquier query que `candidates.build_query` arme (marca+modelo o tokens del
  título) encuentra lo esperado sin tener que precomputar cada combinación posible.
