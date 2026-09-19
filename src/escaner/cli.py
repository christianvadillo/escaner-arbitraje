"""cli.py — entrypoint `escaner`. Cada subcomando hace una sola cosa; la orquestación de
verdad vive en `scanner.py`/`paper/ledger.py`, esto solo arma dependencias desde `Settings` y
formatea la salida.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from escaner import config, report, scanner, store
from escaner.config import Settings
from escaner.economics.profit import CostAssumptions, break_even_sale_price, compute_profit, max_purchase_price
from escaner.economics.taxes import PRESETS
from escaner.http import PoliteClient
from escaner.matching.normalize import features
from escaner.matching.score import LabeledPair, calibrate, evaluate, score_pair, threshold_for_precision
from escaner.meli import oauth
from escaner.meli.client import MLClient
from escaner.meli.fake import FakeMeLiBackend, build_fake_http_client
from escaner.paper import ledger
from escaner.paper.stats import PaperPosition
from escaner.sources.csv_import import CsvSource
from escaner.sources.fixtures import FixturesSource, ml_catalog
from escaner.sources.keepa import KeepaDealFilter, KeepaSource
from escaner.sources.promodescuentos import PromodescuentosSource

_TRUTHY = {"1", "true", "match", "si", "sí", "yes", "y"}


# ── demo ──────────────────────────────────────────────────────────────────────────────────


def _simulate_paper_history(opportunities, n_days: int = 15, seed: int = 7) -> list[PaperPosition]:
    """SOLO para `escaner demo`: replica las oportunidades reales que sí se detectaron a lo
    largo de varios días con una utilidad marcada perturbada, para poder enseñar el veredicto
    de `summarize()` con muestra suficiente sin esperar 10 días de verdad. No es evidencia de
    que la brecha real se sostenga — para eso está `paper mark` + `paper report` sobre datos
    reales."""
    if not opportunities:
        return []
    rng = random.Random(seed)
    positions: list[PaperPosition] = []
    today = datetime.now(UTC).date()
    for day_offset in range(n_days):
        day = (today - timedelta(days=day_offset)).isoformat()
        for i, opp in enumerate(opportunities):
            if rng.random() < 0.3:
                continue
            decay = max(0.0, rng.gauss(0.82, 0.35))
            marked = opp.profit.net_profit * decay
            positions.append(
                PaperPosition(
                    position_id=f"sim-{day}-{i}",
                    detection_day=day,
                    capital=opp.profit.capital_invested,
                    expected_profit=opp.profit.net_profit,
                    marked_profit=round(marked, 2),
                    deal_hours=rng.uniform(2, 96),
                    deal_ended=rng.random() < 0.7,
                )
            )
    return positions


def cmd_demo(_args: argparse.Namespace) -> int:
    from escaner.paper.stats import summarize

    # `from_env({})` a propósito (no `from_env()`): demo debe ser 100% offline y reproducible
    # sin importar qué `.env` tenga el operador — todos los DEMÁS comandos sí leen `os.environ`.
    settings = Settings.from_env({})
    conn = store.connect(":memory:")
    items, products = ml_catalog()
    ml_client = MLClient(http=build_fake_http_client(FakeMeLiBackend(items, products)))

    print("=== escaner demo (100% offline: fixtures + MockTransport, sin red) ===\n")
    summary = scanner.scan(FixturesSource(), ml_client, conn, settings)
    print(
        f"Ofertas vistas: {summary.n_offers} · en revisión: {summary.n_review} · "
        f"sin candidato útil: {summary.n_no_candidate} · oportunidades: {summary.n_opportunities}\n"
    )
    print(report.opportunities_markdown(summary.opportunities, top_n=5))

    print("\n=== Papel (marcas simuladas — ver docstring de _simulate_paper_history) ===\n")
    simulated = _simulate_paper_history(list(summary.opportunities))
    print(report.paper_report_markdown(summarize(simulated)))
    return 0


# ── scan ──────────────────────────────────────────────────────────────────────────────────


def _load_ml_client(settings: Settings) -> MLClient | None:
    token = config.load_token(settings.token_path)
    if token is None:
        return None
    http = httpx.Client(base_url="https://api.mercadolibre.com")
    if token.is_expired():
        refreshed = oauth.refresh(http, settings.ml_app_id, settings.ml_client_secret, token.refresh_token)
        config.save_token(settings.token_path, refreshed)  # persistir ANTES de usarlo (ver meli/oauth.py)
        token = refreshed
    return MLClient(http=http, access_token=token.access_token)


def _load_llm_client(settings: Settings):
    """`None` si el juez está apagado o si el extra `llm` no está instalado — degradar a "sin
    juez" es correcto (la banda "review" simplemente no se escala), no un error fatal."""
    if not settings.llm_enabled:
        return None
    try:
        import anthropic
    except ImportError:
        print("ESCANER_LLM_ENABLED=true pero falta el extra opcional: pip install -e .[llm]", file=sys.stderr)
        return None
    return anthropic.Anthropic()  # lee ANTHROPIC_API_KEY del entorno (estándar del SDK)


def cmd_scan(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ml_client = _load_ml_client(settings)
    if ml_client is None:
        print(
            "No hay token de Mercado Libre guardado. Casi todos los endpoints que el escáner "
            "necesita (search, products/search, listing_prices, …) devuelven 403 sin OAuth de "
            "usuario (ESPECIFICACION §2). Corre `escaner auth-url`, autoriza en el navegador y "
            "luego `escaner auth-code CODE`.",
            file=sys.stderr,
        )
        return 1

    if args.source == "csv" and not args.csv:
        print("--csv es obligatorio con --source csv", file=sys.stderr)
        return 1
    source = {
        "csv": lambda: CsvSource(args.csv),
        "promodescuentos": lambda: PromodescuentosSource(
            PoliteClient(settings.user_agent, store.connect(settings.db_path), settings.min_host_interval_s)
        ),
        "keepa": lambda: KeepaSource(settings.keepa_key, httpx.Client(), KeepaDealFilter()),
    }[args.source]()

    llm_client = _load_llm_client(settings)
    conn = store.connect(settings.db_path)
    summary = scanner.scan(source, ml_client, conn, settings, llm_client=llm_client, limit=args.limit)
    print(report.opportunities_markdown(summary.opportunities))
    print(f"\n{summary.n_offers} ofertas · {summary.n_opportunities} oportunidades · corrida #{summary.run_id}")
    if args.csv_out:
        Path(args.csv_out).write_text(report.opportunities_csv(summary.opportunities), encoding="utf-8")
        print(f"CSV escrito en {args.csv_out}")
    return 0


# ── calc ──────────────────────────────────────────────────────────────────────────────────


def cmd_calc(args: argparse.Namespace) -> int:
    if args.perfil not in PRESETS:
        print(f"Perfil desconocido: {args.perfil!r} (válidos: {sorted(PRESETS)})", file=sys.stderr)
        return 1
    assumptions = CostAssumptions(
        listing_type=args.tipo,
        reputation=args.reputacion,
        tax=PRESETS[args.perfil],
    )
    p = compute_profit(args.venta, args.compra, category_id=args.categoria, weight_g=args.peso, assumptions=assumptions)
    print(f"Perfil fiscal: {args.perfil} · tipo de publicación: {args.tipo} · reputación: {args.reputacion}\n")
    for label, value in p.lines():
        print(f"  {label:38s} {value:12,.2f}")
    print(f"\n  ROI: {p.roi:.1%}  ·  Margen: {p.margin:.1%}")
    for n in p.notes:
        print(f"  Nota: {n}")

    be = break_even_sale_price(args.compra, args.categoria, args.peso, assumptions)
    print(f"\n  Precio de equilibrio (utilidad = 0): {'$' + format(be, ',.2f') if be is not None else 'no alcanzable'}")
    max_compra = max_purchase_price(args.venta, 0.20, args.categoria, args.peso, assumptions)
    print(f"  Compra máxima para ROI 20% vendiendo a ${args.venta:,.2f}: ${max_compra:,.2f}")
    return 0


# ── match / match-eval ──────────────────────────────────────────────────────────────────


def cmd_match(args: argparse.Namespace) -> int:
    a, b = features(args.titulo_a), features(args.titulo_b)
    s = score_pair(a, b, args.ratio)
    print(f"Decisión: {s.decision}  ·  probabilidad: {s.prob:.2%}")
    if s.veto:
        print(f"Veto: {s.veto}")
    for r in s.reasons:
        print(f"  - {r}")
    return 0


def cmd_match_eval(args: argparse.Namespace) -> int:
    import csv

    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    pairs = [
        LabeledPair(
            features(row["offer_title"]),
            features(row["candidate_title"]),
            row["label"].strip().lower() in _TRUTHY,
            float(row["price_ratio"]) if row.get("price_ratio") else None,
        )
        for row in rows
    ]
    weights = calibrate(pairs)
    rows_eval = evaluate(pairs, weights)
    print(f"{len(pairs)} pares cargados\n")
    print("umbral  precision  recall     f1  n_pred")
    for r in rows_eval:
        print(f"{r.threshold:6.2f}  {r.precision:9.2%} {r.recall:6.2%} {r.f1:6.2%}  {r.n_pred:6d}")
    t = threshold_for_precision(rows_eval, 0.97)
    print(f"\nUmbral más bajo con precisión >= 97%: {t if t is not None else 'ninguno alcanza 97%'}")
    print("\nPesos calibrados:")
    for k, v in weights.items():
        print(f"  {k:10s} {v:+.3f}")
    return 0


# ── paper ─────────────────────────────────────────────────────────────────────────────────


def cmd_paper_mark(_args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    ml_client = _load_ml_client(settings)
    if ml_client is None:
        print("No hay token de ML guardado; corre `escaner auth-url`/`auth-code` primero.", file=sys.stderr)
        return 1
    conn = store.connect(settings.db_path)
    polite = PoliteClient(settings.user_agent, conn, settings.min_host_interval_s)
    n = ledger.mark_all(conn, ml_client, settings, polite_client=polite)
    print(f"{n} posiciones marcadas a mercado.")
    return 0


def cmd_paper_report(_args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    conn = store.connect(settings.db_path)
    print(report.paper_report_markdown(ledger.paper_summary(conn)))
    return 0


# ── auth ──────────────────────────────────────────────────────────────────────────────────


def cmd_auth_url(_args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    print(oauth.authorization_url(settings.ml_app_id, settings.ml_redirect_uri))
    return 0


def cmd_auth_code(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    with httpx.Client() as http:
        token = oauth.exchange_code(
            http, settings.ml_app_id, settings.ml_client_secret, settings.ml_redirect_uri, args.code
        )
    config.save_token(settings.token_path, token)
    print(f"Token guardado en {settings.token_path} (expira {token.expires_at:.0f}).")
    return 0


def cmd_init_db(_args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    store.connect(settings.db_path)
    print(f"Base lista en {settings.db_path} (schema v{store.SCHEMA_VERSION}).")
    return 0


# ── argparse ──────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="escaner", description="Escáner de arbitraje retail MX → Mercado Libre")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="corre todo el flujo offline con fixtures").set_defaults(func=cmd_demo)

    sp = sub.add_parser("scan", help="corre una fuente de verdad (requiere token de ML)")
    sp.add_argument("--source", required=True, choices=["promodescuentos", "keepa", "csv"])
    sp.add_argument("--csv", help="ruta al CSV (con --source csv)")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--csv-out", help="además del reporte en pantalla, escribe las oportunidades a este CSV")
    sp.set_defaults(func=cmd_scan)

    cp = sub.add_parser("calc", help="desglose de utilidad neta para una compra/venta")
    cp.add_argument("--compra", type=float, required=True)
    cp.add_argument("--venta", type=float, required=True)
    cp.add_argument("--categoria", default=None)
    cp.add_argument("--peso", type=int, default=500, dest="peso")
    cp.add_argument("--perfil", default="plataformas")
    cp.add_argument("--tipo", default="gold_special")
    cp.add_argument("--reputacion", default="green")
    cp.set_defaults(func=cmd_calc)

    mp = sub.add_parser("match", help="puntúa un par de títulos")
    mp.add_argument("titulo_a")
    mp.add_argument("titulo_b")
    mp.add_argument("--ratio", type=float, default=None, help="precio ML / precio retailer")
    mp.set_defaults(func=cmd_match)

    mep = sub.add_parser("match-eval", help="calibra y evalúa sobre un CSV de pares etiquetados")
    mep.add_argument("csv")
    mep.set_defaults(func=cmd_match_eval)

    paper = sub.add_parser("paper", help="posiciones de papel")
    paper_sub = paper.add_subparsers(dest="paper_command", required=True)
    paper_sub.add_parser("mark", help="re-cotiza las posiciones abiertas").set_defaults(func=cmd_paper_mark)
    paper_sub.add_parser("report", help="veredicto + estadísticas del papel").set_defaults(func=cmd_paper_report)

    sub.add_parser("auth-url", help="URL de autorización de Mercado Libre").set_defaults(func=cmd_auth_url)
    acp = sub.add_parser("auth-code", help="canjea el code por un token y lo guarda")
    acp.add_argument("code")
    acp.set_defaults(func=cmd_auth_code)

    sub.add_parser("init-db", help="crea/migra la base sqlite").set_defaults(func=cmd_init_db)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
