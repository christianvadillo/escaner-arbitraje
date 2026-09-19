# Operación

## 1. Autorizar Mercado Libre (una vez)

Casi todo lo que el escáner necesita de la API de ML requiere OAuth de usuario — verificado en
vivo sin token: `search`, `products/search`, `listing_prices`, etc. devuelven 403/401 (ver
[`docs/ESPECIFICACION.md`](ESPECIFICACION.md) §2). Sin autorizar, solo `escaner demo` funciona.

```bash
cp .env.example .env
# edita .env: ESCANER_ML_APP_ID / ESCANER_ML_CLIENT_SECRET / ESCANER_ML_REDIRECT_URI
# (créala en https://developers.mercadolibre.com.mx/ si no tienes una app todavía)

escaner auth-url
# abre la URL impresa en tu navegador, autoriza tu cuenta, copia el `code` de la redirección

escaner auth-code EL_CODE_QUE_TE_DIO_ML
# guarda el token en data/ml_token.json (permisos 0600, escritura atómica)
```

El token expira en ~3h (`expires_in`). `escaner scan`/`escaner paper mark` lo refrescan solos
cuando hace falta — y el `refresh_token` es de un solo uso: el nuevo se persiste ANTES de que el
comando use el `access_token` nuevo (si el proceso muriera entre refrescar y guardar, prefieres
perder el acceso y volver a autorizar a perder la capacidad de refrescar). No hace falta volver
a correr `auth-url`/`auth-code` salvo que revoques el permiso desde tu cuenta de ML.

## 2. Scan diario (cron / launchd)

```bash
# crontab -e (todos los días 8am, hora del sistema)
0 8 * * * cd /ruta/a/escaner-arbitraje && .venv/bin/escaner scan --source promodescuentos --limit 40 >> data/scan.log 2>&1
```

En macOS, un `launchd` equivalente (`~/Library/LaunchAgents/mx.escaner.scan.plist`) con
`StartCalendarInterval` en vez de una línea de cron cumple lo mismo. Cada corrida:

1. Trae ofertas de la fuente pedida (respeta `min_host_interval_s` y robots.txt si es una fuente
   scrapeada; Keepa no pasa por eso, es API autenticada).
2. Deduplica contra lo visto en los últimos 3 días (`scanner.DEDUPE_WINDOW_DAYS`).
3. Empareja, cotiza y costea; lo que cruza `min_roi`/`min_profit` se persiste como `Opportunity`
   y abre una posición de papel automáticamente.
4. Imprime la tabla de oportunidades de esa corrida a stdout (o al log si rediriges).

Para Keepa (`--source keepa`) hace falta `ESCANER_KEEPA_KEY` en `.env`; para CSV
(`--source csv --csv ruta.csv`) no hace falta token de nada más que ML.

## 3. Marcar el papel a mercado (diario o cada pocas horas)

```bash
0 */6 * * * cd /ruta/a/escaner-arbitraje && .venv/bin/escaner paper mark >> data/mark.log 2>&1
```

Re-cotiza cada posición abierta contra el precio competitivo ACTUAL de ML y contra la vida
observable de la oferta de origen (Keepa: reconsulta el ASIN; retailers en
`allowed_product_hosts`: re-fetch JSON-LD cortés, máx. 1 vez/día; todo lo demás —
Promodescuentos en particular— queda censurado: nunca se observa si la oferta sigue viva, solo
las horas desde publicación). Cierra sola las posiciones que ya cumplieron el horizonte de 30
días. Correrlo más seguido que "unas horas" no gana información nueva y sí gasta cupo de
Keepa/riesgo de bloqueo de más.

## 4. Leer el reporte y decidir GO / KILL

```bash
escaner paper report
```

Imprime el veredicto preregistrado (ver [README](../README.md) y
[`docs/ESPECIFICACION.md`](ESPECIFICACION.md) §1) más las estadísticas que lo sostienen:
tasa de acierto, utilidad esperada por posición con IC90%, ROI con IC90%, decaimiento
(utilidad marcada / esperada — qué tanto sobrevivió la brecha detectada), vida mediana de la
oferta y sobrevivencia a 24h/72h.

**No decidas con menos de 30 posiciones marcadas en 10 días distintos** — el propio reporte lo
dice (`verdict` empieza con `"INSUFICIENTE"` mientras no se cumpla). Con muestra suficiente:

- `IC90% > 0` en la utilidad por posición **y** vida mediana > 6h **y** el ritmo de oportunidades
  × utilidad media alcanza tu meta mensual → arriesga capital REAL, pero **pequeño**: el papel
  no midió cancelaciones de compra, stock limitado por cliente, tiempo real de venta ni
  devoluciones/fraude.
- `IC90% < 0` → **KILL**. No sigas alimentando la fuente ni bajando umbrales hasta que "salga
  algo": eso es p-hacking sobre tu propio escáner.
- El IC cruza cero → sigue acumulando; no es ni GO ni KILL todavía.

## 5. Migraciones / mantenimiento de la base

```bash
escaner init-db   # idempotente: crea data/escaner.db si no existe, migra si hace falta
```

`store.py` versiona el esquema con `PRAGMA user_version`; una base ya al día no se toca.

## 6. Cuándo tocar `.env` en producción

- Subir `ESCANER_MIN_ROI`/`ESCANER_MIN_PROFIT` si el papel muestra brecha positiva pero muy
  angosta (mejor perder oportunidades marginales que arriesgar capital en el margen de error).
- `ESCANER_LLM_ENABLED=true` (+ `pip install -e .[llm]` + `ANTHROPIC_API_KEY` en el entorno) si
  la banda "revisar" (0.60-0.90 de probabilidad) está descartando demasiadas ofertas que a ojo
  sí son el mismo producto — el juez solo puede subir a match con confianza ≥ 0.9 o bajar a
  no_match, nunca anula un veto.
- `ESCANER_ALLOWED_PRODUCT_HOSTS` solo debería crecer con hosts que de verdad no bloqueen
  (confirma con una corrida manual antes de agregarlo — un host nuevo que bloquea apaga esa
  fuente para el resto de la corrida en la que se detecte, pero vale la pena no descubrirlo en
  producción).

## Troubleshooting rápido

| Síntoma | Causa probable |
|---|---|
| `escaner scan` dice "No hay token de Mercado Libre guardado" | Corre `auth-url`/`auth-code` (§1). |
| Una fuente deja de traer nada a media corrida | Esa fuente se bloqueó (403/429/503 o CAPTCHA en el cuerpo) y `PoliteClient` la apagó para el resto de la corrida — es la política correcta, no un bug. Revisa `data/scan.log`. |
| `escaner paper report` dice "INSUFICIENTE" por semanas | Necesitas más corridas de `scan`/`paper mark`, no un umbral más permisivo. |
| Los montos de comisión/envío traen la nota "estimado por tabla" | No hay token de ML activo en ese momento, o `ApiFeeModel`/`ApiShippingModel` no pudieron cotizar y cayeron al modelo de tabla — revisa que el token no haya expirado. |
