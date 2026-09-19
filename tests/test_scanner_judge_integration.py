"""scanner.py + judge.py: cuando `settings.llm_enabled` y hay cliente LLM, una oferta que
quedó en banda "review" (ninguna candidata cruzó 0.90) puede rescatarse si el juez confirma el
match con confianza alta — y su `comparables` no debe quedar vacío (si no, `pricing.py` no
tendría con qué fijar precio y la oportunidad "rescatada" se descartaría en silencio)."""

from __future__ import annotations

from dataclasses import replace

from escaner import scanner, store
from escaner.config import Settings
from escaner.judge import JuezOut
from escaner.meli.client import MLClient
from escaner.meli.fake import FakeMeLiBackend, build_fake_http_client
from escaner.sources.fixtures import ml_catalog
from escaner.sources.fixtures import offers as fixture_offers


class _FakeMessages:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return type("R", (), {"parsed_output": self.parsed_output, "stop_reason": "end_turn"})()


class _FakeAnthropic:
    def __init__(self, parsed_output):
        messages = _FakeMessages(parsed_output)
        self.beta = type("Beta", (), {"messages": messages})()

    @property
    def calls(self):
        return self.beta.messages.calls


def _ml_client() -> MLClient:
    items, products = ml_catalog()
    return MLClient(http=build_fake_http_client(FakeMeLiBackend(items, products)))


def _switch_offer():
    return next(o for o in fixture_offers() if o.guid == "fx-switch-oled")


def _cheap_switch_offer():
    """Cuando el juez rescata una oferta, `comparables` queda con UN solo candidato (el que
    escaló) en vez de los ~4 de `cr.matches` — por diseño (ver `evaluate_offer`), así que el
    precio de venta usa un solo dato en vez de un p25. Para probar el mecanismo (y no la
    economía específica del fixture original) se abarata la compra lo suficiente para que la
    utilidad sea positiva incluso con un solo comparable — pero sin pasar la relación de
    precios de 2× (`price_pen` en matching/score.py), o el propio matcher la descartaría por
    parecer "otro producto" antes de que el juez opine."""
    return replace(_switch_offer(), price=3800.0, guid="fx-switch-oled-cheap-test")


def test_review_band_offer_is_rescued_when_judge_confirms_match():
    settings = Settings.from_env({"ESCANER_LLM_ENABLED": "true"})
    ml_client = _ml_client()
    fee_model, shipping_model = scanner._build_fee_shipping_models(ml_client)
    llm_client = _FakeAnthropic(JuezOut(same_product=True, confidence=0.95))

    offer = _cheap_switch_offer()
    opp, reason = scanner.evaluate_offer(offer, ml_client, settings, fee_model, shipping_model, llm_client=llm_client)

    assert reason == "match"
    assert opp is not None
    assert "juzgado_por_llm" in opp.flags
    assert opp.sale_price > 0
    assert opp.comparables  # el fix: no debe quedar vacío
    assert llm_client.calls  # de verdad se le preguntó


def test_review_band_offer_stays_in_review_without_llm_enabled():
    settings = Settings.from_env({})  # llm_enabled=False por default
    ml_client = _ml_client()
    fee_model, shipping_model = scanner._build_fee_shipping_models(ml_client)
    llm_client = _FakeAnthropic(JuezOut(same_product=True, confidence=0.95))

    offer = _switch_offer()
    opp, reason = scanner.evaluate_offer(offer, ml_client, settings, fee_model, shipping_model, llm_client=llm_client)

    assert opp is None
    assert reason == "review"
    assert llm_client.calls == []  # ni se le preguntó: el juez está apagado


def test_judge_disagreement_keeps_it_out_of_opportunities():
    settings = Settings.from_env({"ESCANER_LLM_ENABLED": "true"})
    ml_client = _ml_client()
    fee_model, shipping_model = scanner._build_fee_shipping_models(ml_client)
    llm_client = _FakeAnthropic(JuezOut(same_product=False, confidence=0.9, differences=["capacidad distinta"]))

    offer = _switch_offer()
    opp, reason = scanner.evaluate_offer(offer, ml_client, settings, fee_model, shipping_model, llm_client=llm_client)

    assert opp is None
    assert reason == "review"


def test_full_scan_wires_llm_client_and_judge_cache_through():
    """Regresión de integración end-to-end: `scan()` debe pasar `llm_client` y el mismo `conn`
    como caché del juez hasta `evaluate_offer`, sin que el llamador tenga que hacerlo a mano.
    No depende de que la rescatada supere el umbral de utilidad (eso ya se prueba aparte con un
    precio controlado): solo de que el juez de verdad se llamó y quedó cacheado."""
    from escaner.sources.fixtures import FixturesSource

    settings = Settings.from_env({"ESCANER_LLM_ENABLED": "true"})
    ml_client = _ml_client()
    llm_client = _FakeAnthropic(JuezOut(same_product=True, confidence=0.95))
    conn = store.connect(":memory:")

    scanner.scan(FixturesSource(), ml_client, conn, settings, llm_client=llm_client)

    assert llm_client.calls  # switch_oled (y quizá otras) cayeron en review y se le preguntó
    n_cached = conn.execute("SELECT COUNT(*) FROM judge_cache").fetchone()[0]
    assert n_cached >= 1  # la caché del juez vive en el MISMO conn que el resto del escaneo
