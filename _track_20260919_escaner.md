# PLAN — Escáner de arbitraje retail MX → Mercado Libre

Construir un escáner que detecte liquidaciones, las empareje con Mercado Libre y calcule utilidad
NETA (comisión, envío, impuestos 2026, devoluciones, capital), y que se use primero en papel
con una regla GO/KILL preregistrada. Repo local + remoto privado en GitHub.

## ANÁLISIS

- Edge primero: la brecha bruta no importa; el costo total de revender ronda 25–35 % del precio.
  La hipótesis es falsable con posiciones de papel marcadas a mercado.
- El foso técnico es el emparejamiento sin identificador común; el error caro es el falso
  positivo (comprar el producto equivocado) → vetos duros antes de cualquier similitud.

## INVESTIGACIÓN

- ML (prueba en vivo 19-sep-2026): búsqueda, catálogo, highlights, trends y listing_prices exigen
  OAuth de usuario; `domain_discovery` y `categories/{id}/attributes` siguen públicos.
- Impuestos 2026 (LIF 2026): ISR 2.5 % con RFC (era 1 %), 20 % sin RFC; IVA 8 % / 16 %.
- Envío gratis obligatorio ≥ $299; cargo fijo < $299 solo Flex/ME1 desde abr-2026.
- Fuentes: PA-API 5 muerta; Keepa soporta MX (domain 11); Walmart (PerimeterX), Liverpool (WAF)
  y Coppel (timeouts) bloquean → fuera; Promodescuentos RSS activo (uso personal; su ToS impide
  reproducir → licencia si se vende).

## CAMBIOS

- [x] Núcleo (Opus): `economics/{taxes,fees,profit}.py`, `matching/{normalize,score}.py`, `paper/stats.py` + 39 tests
- [x] Vetos: GTIN, accesorio, condición, marca, paquete, capacidad, variante (Pro/Max/Note…), generación, modelo corto, código casi igual — 21/21 pares de prueba correctos
- [x] Especificación `docs/ESPECIFICACION.md` con regla GO/KILL preregistrada
- [x] Pipeline (worker Sonnet): config, PoliteClient (robots, crawl-delay, alto ante bloqueo, lista negra), fuentes (Promodescuentos RSS, Keepa, JSON-LD, CSV), cliente ML + OAuth + fake, candidatos, juez LLM, pricing, scanner, store, ledger de papel, reporte, CLI, tests, docs, CI
- [x] Revisión del hilo principal
- [x] Commit inicial + repo remoto privado + push
- [x] Vault (diario-global) + memoria

## VALIDACIÓN

- Núcleo: 39 tests verdes. El umbral de $299 hace la utilidad no monótona (vender a $298 deja
  más que a $305) — test explícito y aviso en el reporte.

## BITÁCORA

- 2026-09-19 — Investigación (5 agentes) + núcleo escrito. Primer matcher con sesgo a descartar
  positivos (bias −6); se ajustó a −4.5 y se añadieron vetos de variante/generación que mataron
  los falsos positivos difíciles (Buds 2 vs 2 Pro, iPhone 15 vs 14, Redmi 13 vs Note 13).
  "térmica" se marcaba accesorio por contener "mica" → coincidencia por palabra completa.
- 2026-09-19 — Revisión del hilo principal: (1) los fixtures de la demo usaban descuentos de
  40–45 % en TODO y 9/11 ofertas salían con ROI ~33 %: enseñaban lo contrario de la tesis. Se
  recalibraron a liquidaciones realistas (20–37 %): ahora pasan 2/11 y queda visible que ~20–25 %
  de descuento es break-even después de costos. (2) La lista negra (Walmart, Liverpool, Coppel)
  comparaba el host exacto; ahora cubre subdominios (m.liverpool.com.mx) con test. 154 tests.
- 2026-09-19 — **Tratamiento de portafolio** (mismo criterio que copiloto-reclamos, tras decidir
  no perseguir el camino comercial). README con "qué demuestra": matar la propia tesis con
  datos (2/11, break-even 20–25 %), emparejamiento asimétrico con 100 % de precisión sobre 41
  pares, economía completa con la no monotonía del umbral $299, validación en papel con KM +
  block bootstrap y regla preregistrada, y recolección respetuosa. Salidas reales de
  `match-eval` y `demo` pegadas, diagrama Mermaid verificado renderizándolo, y
  `docs/GUION_ENTREVISTA.md` (3 min + FAQ, incluida "¿no es un fracaso entonces?").

---

## Anexo — bitácora del constructor (Sonnet), integrada aquí para tener un solo audit trail

# PLAN — Escáner de arbitraje retail MX → Mercado Libre

## ANÁLISIS

Repo nuevo (no git). Ya implementado y con tests (NO tocar semántica): `economics/{taxes,fees,profit}.py`,
`matching/{normalize,score}.py`, `paper/stats.py` + sus tests (39 tests pasan en baseline).

Falta construir: config, models, http cliente cortés, sources (promodescuentos/keepa/jsonld/csv/fixtures),
meli (oauth/client/fake), candidates, judge (LLM opcional), pricing, scanner (orquestador), store (sqlite),
paper/ledger, report, cli, tests nuevos, docs/ops.

Restricciones duras: sin red real en tests/demo; solo httpx+pydantic(+anthropic opcional); stdlib para
XML/HTML/sqlite/robots; sin git commits; venv ya tiene anthropic 1.7 con `beta.messages.parse` (verificado
contra el SDK instalado: firma y excepciones coinciden con la especificación).

## INVESTIGACIÓN

- Verificado en el SDK instalado (`.venv/lib/python3.13/site-packages/anthropic`):
  `client.beta.messages.parse(model, max_tokens, betas, fallbacks, output_config={"effort":...},
  messages, output_format=PydanticModel)` → `ParsedBetaMessage.parsed_output`. `stop_reason` incluye
  `"refusal"`. Excepciones: `RateLimitError(APIStatusError(APIError))`, `APIConnectionError(APIError)` —
  cadena de except coincide con la spec.
- Núcleo leído completo: taxes/fees/profit (umbral $299 no-monótono, perfiles fiscales 2026), matching
  (vetos duros + regresión logística calibrable), paper/stats (Kaplan–Meier + bootstrap por bloque de día).
- Tests existentes fijan el contrato: no modificar firmas de `compute_profit`, `score_pair`, `summarize`, etc.

## CAMBIOS

- [x] models.py
- [x] http.py (PoliteClient + caché condicional + bloqueo + robots + blacklist)
- [x] meli/oauth.py, meli/client.py, meli/fake.py
- [x] config.py (Settings.from_env + token atómico 0600)
- [x] sources/{base,csv_import,promodescuentos,keepa,jsonld,fixtures}.py
- [x] candidates.py
- [x] judge.py
- [x] pricing.py
- [x] store.py (sqlite, PRAGMA user_version)
- [x] scanner.py
- [x] paper/ledger.py
- [x] report.py
- [x] cli.py + entrypoint editable install
- [x] tests nuevos (sin red)
- [x] docs (README, ARQUITECTURA, OPERACION), .env.example, .gitignore, Makefile, CI, pares_etiquetados.csv

## VALIDACIÓN

- [x] `ruff check` limpio
- [x] `ruff format --check` limpio
- [x] `pytest` completo (existentes + nuevos) en verde
- [x] `escaner demo` corre offline

## BITÁCORA

- 2026-09-19 10:00: leído spec + núcleo + SDK anthropic instalado; diseño completo; inicia implementación.
- 2026-09-19 12:30: módulos base (models/http/meli/config/sources) escritos y con smoke-test manual
  contra los fixtures. Bugs reales atrapados en esta pasada (no en tests, en ejecución directa):
  `urllib.robotparser.RobotFileParser.can_fetch` devuelve `False` incondicional si no se llama
  `.modified()` después de `.parse()` (gotcha documentado de la stdlib) — corregido en `http.py`.
  `PoliteClient` no ponía `conn.row_factory = sqlite3.Row` él mismo → asumía que el llamador ya lo
  había hecho (rompía uso standalone) — corregido para que el módulo sea autosuficiente.
- 2026-09-19 14:00: candidates/pricing/store/scanner/ledger/report/cli escritos. Fixtures iteradas
  contra el pipeline real: precios iniciales dejaban solo 1/11 oportunidades reales por encima de
  min_roi/min_profit — recalibradas con `max_purchase_price(target_roi=0.30)` del propio núcleo
  (no a mano) → 9/11. Bug real: `paper/ledger.py::mark_all` no reproducía `buy_box_price` al
  remarcar (no se persistía en `Opportunity`/sqlite) → utilidad marcada sesgada al alza cuando el
  buy box era la restricción vinculante; se agregó el campo y se re-cotiza el buy box fresco del
  catálogo en cada `mark_all` (con fallback al último conocido si la API falla).
- 2026-09-19 15:30: 130 tests nuevos escritos por módulo. Bug real: varias pruebas de `PoliteClient`
  usaban el `sleep`/`min_host_interval_s` por defecto (10s) con dos peticiones al mismo host
  (robots.txt + página) → *sleeps* de verdad sumando ~70s de wall-clock en la suite. Corregido
  (`min_host_interval_s=0.0` en las pruebas que no prueban temporización) — suite bajó a <1s.
- 2026-09-19 16:15: conecté `judge.py` a `scanner.py` (`_escalate_to_judge`) — la especificación no
  lo pedía explícito en la sección de `scanner.py`, pero sin esto `llm_enabled/llm_model/llm_effort`
  eran configuración muerta. Bug real atrapado por un test de integración: al rescatar una oferta de
  la banda "review", `comparables` quedaba vacío (el candidato promovido no estaba en `cr.matches`)
  → `pricing.py` no podía fijar precio y la oportunidad "rescatada" se descartaba en silencio;
  corregido. Bug real separado en `cli.py`: los 6 subcomandos que NO son `demo` llamaban
  `Settings.from_env({})` (dict vacío) en vez de `Settings.from_env()` → ignoraban `.env`/entorno
  por completo; corregido (`demo` sigue ignorándolo a propósito, con comentario explícito).
- 2026-09-19 17:00: docs (README con ejemplos reales de `calc`/`demo` capturados de una corrida real,
  ARQUITECTURA, OPERACION), `.env.example`, `.gitignore`, `Makefile`, CI, `data/pares_etiquetados.csv`
  (41 pares: 21 del núcleo + 20 nuevos) validado con `escaner match-eval` (precisión 100% en todos
  los umbrales probados, recall 84% en el umbral de producción 0.90). Build completo: `ruff check` y
  `ruff format --check` limpios, **144 tests verdes en <1s** (`make test`), `escaner demo` y
  `escaner calc` corren offline vía el entrypoint instalado. Deviaciones vs. spec documentadas en
  `docs/ARQUITECTURA.md` (`ml_user_id` opcional en Settings, `buy_box_price` en Opportunity, juez
  LLM conectado a scanner.py). Nota: existe un segundo archivo `_track_20260919_escaner.md` en la
  raíz que yo no creé — documenta la sesión previa que construyó el núcleo protegido; se deja
  intacto (regla "no borrar").
