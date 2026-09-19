"""judge.py: el juez LLM solo puede mover review→match (confianza ≥ 0.9) o review→no_match,
JAMÁS anula un veto, cachea por hash del par, y traduce RateLimitError/APIStatusError/
APIConnectionError/refusal en un `JudgeResult` degradado en vez de tronar el escaneo. El
cliente `anthropic` es enteramente fake — nunca se toca la red ni se instancia el SDK real."""

from __future__ import annotations

import sqlite3

import anthropic
import httpx2
import pytest

from escaner.judge import JuezOut, judge_pair
from escaner.matching.normalize import features
from escaner.matching.score import MatchScore

OFFER = features("Samsung Galaxy A15 128GB Azul Oscuro")
CANDIDATE = features("Celular Samsung Galaxy A15 128 GB Azul Dual SIM")
REVIEW_SCORE = MatchScore(0.75, "review", ("similitud de texto 70%",))
VETOED_SCORE = MatchScore(0.0, "no_match", ("GTIN distinto",), veto="GTIN distinto")


class _FakeMessages:
    def __init__(self, response=None, exception=None):
        self.response = response
        self.exception = exception
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.exception is not None:
            raise self.exception
        return self.response


class _FakeClient:
    def __init__(self, response=None, exception=None):
        self.beta = type("Beta", (), {"messages": _FakeMessages(response, exception)})()

    @property
    def calls(self):
        return self.beta.messages.calls


class _FakeResponse:
    def __init__(self, parsed_output, stop_reason="end_turn"):
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason


def _req() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def test_never_calls_the_llm_when_the_base_score_is_a_veto():
    client = _FakeClient(response=_FakeResponse(JuezOut(same_product=True, confidence=0.99)))
    result = judge_pair(client, OFFER, CANDIDATE, VETOED_SCORE)
    assert result.used_llm is False
    assert result.verdict is VETOED_SCORE
    assert client.calls == []  # ni se le preguntó


def test_promotes_review_to_match_with_high_confidence():
    client = _FakeClient(response=_FakeResponse(JuezOut(same_product=True, confidence=0.95)))
    result = judge_pair(client, OFFER, CANDIDATE, REVIEW_SCORE)
    assert result.verdict.decision == "match"
    assert result.verdict.prob >= 0.90
    assert result.used_llm is True


def test_does_not_promote_with_low_confidence_even_if_same_product():
    client = _FakeClient(response=_FakeResponse(JuezOut(same_product=True, confidence=0.55)))
    result = judge_pair(client, OFFER, CANDIDATE, REVIEW_SCORE)
    assert result.verdict.decision == "review"  # se queda igual: no hay confianza suficiente
    assert result.verdict.prob == REVIEW_SCORE.prob


def test_demotes_to_no_match_when_llm_says_different_products():
    client = _FakeClient(
        response=_FakeResponse(JuezOut(same_product=False, confidence=0.8, differences=["capacidad distinta"]))
    )
    result = judge_pair(client, OFFER, CANDIDATE, REVIEW_SCORE)
    assert result.verdict.decision == "no_match"
    assert "capacidad distinta" in " ".join(result.verdict.reasons)


def test_refusal_stop_reason_keeps_base_score():
    client = _FakeClient(response=_FakeResponse(None, stop_reason="refusal"))
    result = judge_pair(client, OFFER, CANDIDATE, REVIEW_SCORE)
    assert result.verdict is REVIEW_SCORE
    assert result.used_llm is False
    assert result.error is not None


@pytest.mark.parametrize(
    "exception",
    [
        anthropic.RateLimitError("rate limited", response=httpx2.Response(429, request=_req()), body=None),
        anthropic.APIStatusError("server error", response=httpx2.Response(500, request=_req()), body=None),
        anthropic.APIConnectionError(request=_req()),
    ],
)
def test_sdk_errors_degrade_to_base_score_instead_of_raising(exception):
    client = _FakeClient(exception=exception)
    result = judge_pair(client, OFFER, CANDIDATE, REVIEW_SCORE)
    assert result.verdict is REVIEW_SCORE
    assert result.used_llm is False
    assert result.error is not None


def test_caches_by_pair_hash_and_does_not_call_the_llm_twice():
    conn = sqlite3.connect(":memory:")
    client = _FakeClient(response=_FakeResponse(JuezOut(same_product=True, confidence=0.95)))
    first = judge_pair(client, OFFER, CANDIDATE, REVIEW_SCORE, cache_conn=conn)
    second = judge_pair(client, OFFER, CANDIDATE, REVIEW_SCORE, cache_conn=conn)
    assert first.verdict.decision == second.verdict.decision == "match"
    assert len(client.calls) == 1  # la segunda vino de judge_cache


def test_never_overturns_a_veto_even_if_the_llm_would_say_match():
    """Blindaje explícito: aunque el juez esté configurado para responder same_product=True con
    confianza alta, un veto no debe ni preguntársele."""
    client = _FakeClient(response=_FakeResponse(JuezOut(same_product=True, confidence=0.99)))
    result = judge_pair(client, OFFER, CANDIDATE, VETOED_SCORE, cache_conn=sqlite3.connect(":memory:"))
    assert result.verdict.decision == "no_match"
    assert result.verdict.veto == "GTIN distinto"
    assert client.calls == []


def test_no_output_format_returns_error_without_raising():
    client = _FakeClient(response=_FakeResponse(None, stop_reason="end_turn"))
    result = judge_pair(client, OFFER, CANDIDATE, REVIEW_SCORE)
    assert result.used_llm is False
    assert result.error is not None
