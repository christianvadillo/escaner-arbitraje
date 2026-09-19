"""cli.py: `demo` corre offline de punta a punta, `calc`/`match`/`match-eval` imprimen lo
esperado, y (regresión) los comandos que NO son `demo` sí leen `os.environ` — `demo` es el único
que debe ignorarlo a propósito para quedar reproducible sin importar el `.env` del operador."""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from escaner import cli
from escaner.matching.normalize import features
from escaner.matching.score import LabeledPair, calibrate, evaluate, threshold_for_precision

PARES_CSV = Path(__file__).resolve().parent.parent / "data" / "pares_etiquetados.csv"


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


def test_demo_runs_offline_and_prints_opportunities_and_verdict():
    code, out = _run(["demo"])
    assert code == 0
    assert "Oportunidades detectadas" in out
    assert "Veredicto:" in out
    assert "Utilidad neta" in out


def test_demo_ignores_real_environment(monkeypatch):
    # Aunque el entorno tenga un tax_profile inválido, `demo` no debe tronar: usa from_env({}).
    monkeypatch.setenv("ESCANER_TAX_PROFILE", "esto-no-existe")
    code, out = _run(["demo"])
    assert code == 0
    assert "Oportunidades detectadas" in out


def test_calc_prints_breakdown_and_break_even():
    code, out = _run(["calc", "--compra", "1500", "--venta", "2300", "--peso", "800"])
    assert code == 0
    assert "Utilidad neta" in out
    assert "ROI:" in out
    assert "Precio de equilibrio" in out
    assert "Compra máxima para ROI 20%" in out


def test_calc_with_invalid_profile_fails_cleanly():
    code, _out = _run(["calc", "--compra", "100", "--venta", "200", "--perfil", "inventado"])
    assert code == 1


def test_match_reports_decision_and_reasons():
    code, out = _run(["match", "Samsung Galaxy A15 128GB", "Funda para Samsung Galaxy A15", "--ratio", "0.1"])
    assert code == 0
    assert "no_match" in out
    assert "Veto" in out


def test_match_eval_runs_on_the_shipped_seed_csv():
    code, out = _run(["match-eval", str(PARES_CSV)])
    assert code == 0
    assert "pares cargados" in out
    assert "Pesos calibrados" in out
    assert "97%" in out


def test_seed_pairs_calibrate_to_at_least_97_percent_precision():
    """El propio dataset que se versiona (`data/pares_etiquetados.csv`) debe alcanzar la
    precisión objetivo (ESPECIFICACION / cli `match-eval`); si alguien lo edita y la rompe, este
    test debe fallar antes que un GO/KILL real se apoye en un matcher mal calibrado."""
    import csv

    with open(PARES_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 40

    pairs = [
        LabeledPair(
            features(row["offer_title"]),
            features(row["candidate_title"]),
            row["label"].strip().lower() == "true",
            float(row["price_ratio"]),
        )
        for row in rows
    ]
    weights = calibrate(pairs)
    rows_eval = evaluate(pairs, weights)
    threshold = threshold_for_precision(rows_eval, target=0.97)
    assert threshold is not None


def test_init_db_reads_real_environment(tmp_path, monkeypatch):
    db_path = tmp_path / "custom" / "escaner.db"
    monkeypatch.setenv("ESCANER_DB_PATH", str(db_path))
    code, out = _run(["init-db"])
    assert code == 0
    assert db_path.exists()
    assert str(db_path) in out


def test_auth_url_reads_app_id_from_environment(monkeypatch):
    monkeypatch.setenv("ESCANER_ML_APP_ID", "MI_APP_123")
    code, out = _run(["auth-url"])
    assert code == 0
    assert "client_id=MI_APP_123" in out


def test_scan_without_token_fails_with_a_clear_message(tmp_path, monkeypatch):
    monkeypatch.setenv("ESCANER_TOKEN_PATH", str(tmp_path / "no-token-aqui.json"))
    err = io.StringIO()
    with redirect_stderr(err):
        code, _out = _run(["scan", "--source", "csv", "--csv", "no-importa.csv"])
    assert code == 1
    assert "auth-url" in err.getvalue()  # el mensaje remite al flujo de autorización


def test_scan_over_csv_writes_opportunities_csv(tmp_path, monkeypatch):
    """`--csv-out` (reporte CSV de oportunidades, ESPECIFICACION punto 12): corre `scan` sobre
    una fuente CSV local con un cliente de ML fake inyectado vía monkeypatch (cero red) y
    confirma que el CSV se escribe con al menos el encabezado esperado."""
    import escaner.cli as cli_mod
    from escaner.meli.client import MLClient
    from escaner.meli.fake import FakeMeLiBackend, build_fake_http_client
    from escaner.sources.fixtures import ml_catalog

    monkeypatch.setenv("ESCANER_DB_PATH", str(tmp_path / "escaner.db"))
    items, products = ml_catalog()
    fake_client = MLClient(http=build_fake_http_client(FakeMeLiBackend(items, products)))
    monkeypatch.setattr(cli_mod, "_load_ml_client", lambda settings: fake_client)

    csv_in = tmp_path / "ofertas.csv"
    csv_in.write_text(
        "title,price,url,merchant,brand,model\n"
        "Samsung Galaxy A15 128GB Azul Oscuro,2300,https://x/a15,Bodega Aurrera,Samsung,SM-A155M\n",
        encoding="utf-8",
    )
    csv_out = tmp_path / "oportunidades.csv"

    code, out = _run(["scan", "--source", "csv", "--csv", str(csv_in), "--csv-out", str(csv_out)])
    assert code == 0
    assert csv_out.exists()
    header = csv_out.read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("offer_title,merchant,purchase_price")
    assert f"CSV escrito en {csv_out}" in out
