"""judge.py — juez LLM opcional para la banda "revisar" del emparejador (0.60–0.90 de
probabilidad en `matching.score`). Es un desempate, no una autoridad: solo puede mover
review→match (con `confidence >= 0.9`) o review→no_match; JAMÁS se le pregunta sobre un par que
ya vino vetado — un veto es determinista y el juez no tiene información que lo invalide (GTIN
distinto, accesorio, condición distinta, etc. no son cuestión de opinión).

Llamada exacta al SDK `anthropic` 1.x (verificada contra el paquete instalado en `.venv`):
`client.beta.messages.parse(model=..., max_tokens=1000, betas=[...], fallbacks="default",
output_config={"effort": ...}, messages=[...], output_format=JuezOut)` →
`response.parsed_output`. Sin `temperature` (no es parte del contrato pedido). El cliente es
inyectable: nunca se instancia `anthropic.Anthropic()` aquí adentro, así que este módulo no
depende de que exista una API key para poder importarse o probarse.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

import anthropic
from pydantic import BaseModel, Field

from escaner.matching.normalize import ProductFeatures
from escaner.matching.score import MatchScore

BETA_FALLBACK = "server-side-fallback-2026-07-01"
PROMOTE_CONFIDENCE = 0.9
MATCH_FLOOR = 0.90
REVIEW_CEILING = 0.59


class JuezOut(BaseModel):
    same_product: bool
    confidence: float = Field(ge=0.0, le=1.0)
    differences: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class JudgeResult:
    verdict: MatchScore
    used_llm: bool
    raw: JuezOut | None = None
    error: str | None = None


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS judge_cache (
            pair_hash TEXT PRIMARY KEY,
            same_product INTEGER NOT NULL,
            confidence REAL NOT NULL,
            differences TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def pair_hash(offer_title: str, candidate_title: str) -> str:
    return hashlib.sha256(f"{offer_title}␟{candidate_title}".encode()).hexdigest()


def _prompt(offer: ProductFeatures, candidate: ProductFeatures) -> str:
    return (
        "Eres un verificador estricto de catálogo de e-commerce para un escáner de arbitraje: "
        "un falso positivo aquí significa comprar el producto equivocado con dinero real. "
        "Decide si estas dos publicaciones describen EL MISMO producto (mismo modelo y "
        "capacidad; el color no importa; misma condición nuevo/usado; mismo empaque/cantidad). "
        "Ante la duda, same_product=false y explica la diferencia.\n\n"
        f"Publicación A: {offer.raw_title}\n"
        f"Publicación B: {candidate.raw_title}\n"
    )


def _cache_get(conn: sqlite3.Connection, key: str) -> JuezOut | None:
    row = conn.execute(
        "SELECT same_product, confidence, differences FROM judge_cache WHERE pair_hash = ?", (key,)
    ).fetchone()
    if not row:
        return None
    return JuezOut(same_product=bool(row[0]), confidence=row[1], differences=row[2].split("␞") if row[2] else [])


def _cache_put(conn: sqlite3.Connection, key: str, out: JuezOut) -> None:
    conn.execute(
        """
        INSERT INTO judge_cache (pair_hash, same_product, confidence, differences, created_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(pair_hash) DO UPDATE SET
            same_product = excluded.same_product, confidence = excluded.confidence,
            differences = excluded.differences, created_at = excluded.created_at
        """,
        (key, int(out.same_product), out.confidence, "␞".join(out.differences)),
    )
    conn.commit()


def _apply_verdict(base: MatchScore, out: JuezOut) -> MatchScore:
    if base.veto:
        return base  # nunca se llega aquí si el caller respeta el contrato, pero por si acaso
    if out.same_product and out.confidence >= PROMOTE_CONFIDENCE:
        return MatchScore(
            max(base.prob, MATCH_FLOOR),
            "match",
            (*base.reasons, "juez LLM: mismo producto"),
            features=base.features,
        )
    if not out.same_product:
        reason = "juez LLM: " + ("; ".join(out.differences) or "productos distintos")
        return MatchScore(min(base.prob, REVIEW_CEILING), "no_match", (*base.reasons, reason), features=base.features)
    return base  # same_product=True pero confianza < 0.9: no alcanza para mover el veredicto


def judge_pair(
    client: anthropic.Anthropic,
    offer: ProductFeatures,
    candidate: ProductFeatures,
    base_score: MatchScore,
    model: str = "claude-opus-5",
    effort: str = "low",
    cache_conn: sqlite3.Connection | None = None,
) -> JudgeResult:
    """Solo tiene sentido llamarlo para `base_score.decision == "review"`; el caller es quien
    filtra eso (aquí solo se blinda contra un veto, que nunca debe tocarse)."""
    if base_score.veto:
        return JudgeResult(base_score, used_llm=False)

    key = pair_hash(offer.raw_title, candidate.raw_title)
    if cache_conn is not None:
        ensure_schema(cache_conn)
        cached = _cache_get(cache_conn, key)
        if cached is not None:
            return JudgeResult(_apply_verdict(base_score, cached), used_llm=True, raw=cached)

    try:
        response = client.beta.messages.parse(
            model=model,
            max_tokens=1000,
            betas=[BETA_FALLBACK],
            fallbacks="default",
            output_config={"effort": effort},
            messages=[{"role": "user", "content": _prompt(offer, candidate)}],
            output_format=JuezOut,
        )
    except anthropic.RateLimitError as e:
        return JudgeResult(base_score, used_llm=False, error=f"rate limit: {e}")
    except anthropic.APIStatusError as e:
        return JudgeResult(base_score, used_llm=False, error=f"api status {e.status_code}: {e}")
    except anthropic.APIConnectionError as e:
        return JudgeResult(base_score, used_llm=False, error=f"conexión: {e}")

    if response.stop_reason == "refusal":
        return JudgeResult(base_score, used_llm=False, error="el modelo rehusó responder")

    parsed = response.parsed_output
    if parsed is None:
        return JudgeResult(base_score, used_llm=False, error="sin salida estructurada")

    if cache_conn is not None:
        _cache_put(cache_conn, key, parsed)
    return JudgeResult(_apply_verdict(base_score, parsed), used_llm=True, raw=parsed)
