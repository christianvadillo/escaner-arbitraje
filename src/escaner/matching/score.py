"""score.py — ¿son el mismo producto? Probabilidad calibrable + vetos duros.

Un falso positivo aquí es dinero real: compras un iPhone 15 porque "empató" con una funda, o
un 128 GB que en ML se vende como 256 GB. Por eso el diseño es asimétrico:

  1. VETOS deterministas (probabilidad 0, sin importar lo parecido del texto): GTIN distinto,
     accesorio vs producto, nuevo vs reacondicionado, marca distinta, paquete distinto,
     capacidades incompatibles (128 vs 256 GB; 4+128 vs 6+128), variante distinta (Pro, Max,
     Plus, Lite, Note…), generación distinta (iPhone 15 vs 14), modelo corto distinto (A15 vs
     A25) y código de modelo "casi igual" (WH-1000XM5 vs XM4 = otra generación).
  2. GTIN igual → 0.99.
  3. El resto: regresión logística sobre rasgos de texto y atributos. Los pesos por defecto
     son juicio experto; `calibrate` los reajusta con pares etiquetados propios y `evaluate`
     reporta precisión/recall por umbral para escoger el corte con precisión ≥ objetivo.

Bandas: ≥ 0.90 match · 0.60–0.90 revisar (LLM o humano) · < 0.60 descartar.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from escaner.matching.normalize import ProductFeatures, compact_units

MATCH_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.60

FEATURES = ("bias", "char3", "tokens", "model", "short", "brand", "qty", "price_pen")
DEFAULT_WEIGHTS: dict[str, float] = {
    "bias": -4.5,
    "char3": 4.5,
    "tokens": 2.0,
    "model": 3.5,
    "short": 2.0,
    "brand": 1.2,
    "qty": 1.0,
    "price_pen": -2.0,
}


@dataclass(frozen=True)
class MatchScore:
    prob: float
    decision: str  # "match" | "review" | "no_match"
    reasons: tuple[str, ...]
    veto: str | None = None
    features: dict[str, float] | None = None


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    t = f" {text} "
    return {t[i : i + n] for i in range(max(0, len(t) - n + 1))}


def _jaccard(a: set | frozenset, b: set | frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


def quantity_conflict(a: ProductFeatures, b: ProductFeatures) -> str | None:
    """Incompatibles si, para una misma unidad, ningún conjunto contiene al otro:
    {128} vs {256} choca; {4, 128} vs {128} no (al otro le falta el dato de RAM);
    {4, 128} vs {6, 128} sí (variantes distintas)."""
    for unit in set(a.quantities) & set(b.quantities):
        qa, qb = a.quantities[unit], b.quantities[unit]
        if qa and qb and not (qa <= qb or qb <= qa):
            return f"{unit}: {sorted(qa)} vs {sorted(qb)}"
    return None


def model_code_conflict(a: ProductFeatures, b: ProductFeatures) -> str | None:
    """Códigos largos que comparten la mayor parte del prefijo pero difieren = otra versión."""
    if not a.model_codes or not b.model_codes or a.model_codes & b.model_codes:
        return None
    for ca in a.model_codes:
        for cb in b.model_codes:
            shorter = min(len(ca), len(cb))
            if shorter >= 5 and _common_prefix(ca, cb) >= 0.7 * shorter:
                return f"modelo {ca} vs {cb}"
    return None


def veto(a: ProductFeatures, b: ProductFeatures) -> str | None:
    if a.gtins and b.gtins and not (a.gtins & b.gtins):
        return "GTIN distinto"
    if a.accessory != b.accessory:
        return "uno es accesorio y el otro no"
    if a.condition != b.condition:
        return "condición distinta (nuevo vs usado/reacondicionado)"
    if a.brand and b.brand and a.brand != b.brand:
        return f"marca distinta ({a.brand} vs {b.brand})"
    if a.pack_qty != b.pack_qty:
        return f"cantidad por paquete distinta ({a.pack_qty} vs {b.pack_qty})"
    q = quantity_conflict(a, b)
    if q:
        return f"capacidad incompatible ({q})"
    if a.variants != b.variants:
        diff = sorted(a.variants ^ b.variants)
        return f"variante distinta ({', '.join(diff)})"
    if a.bare_numbers and b.bare_numbers and not (a.bare_numbers <= b.bare_numbers or b.bare_numbers <= a.bare_numbers):
        return f"generación/número distinto ({sorted(a.bare_numbers)} vs {sorted(b.bare_numbers)})"
    if a.short_codes and b.short_codes and not (a.short_codes & b.short_codes):
        return f"modelo distinto ({'/'.join(sorted(a.short_codes))} vs {'/'.join(sorted(b.short_codes))})"
    m = model_code_conflict(a, b)
    if m:
        return m
    return None


def feature_vector(a: ProductFeatures, b: ProductFeatures, price_ratio: float | None = None) -> dict[str, float]:
    shared_units = set(a.quantities) & set(b.quantities)
    qty = 1.0 if shared_units else 0.5  # sin unidades en común: no hay evidencia a favor ni en contra
    price_pen = 0.0
    if price_ratio and price_ratio > 0:
        # En arbitraje el precio en ML suele ser 1.1–1.8× el de liquidación; más allá de 2×
        # (o por debajo de 0.5×) casi siempre es otro producto, un paquete o un accesorio.
        price_pen = max(0.0, abs(math.log(price_ratio)) - math.log(2.0))
    return {
        "bias": 1.0,
        "char3": _jaccard(_char_ngrams(compact_units(a.norm)), _char_ngrams(compact_units(b.norm))),
        "tokens": _jaccard(a.token_set, b.token_set),
        "model": 1.0 if a.model_codes & b.model_codes else 0.0,
        "short": 1.0 if a.short_codes & b.short_codes else 0.0,
        "brand": 1.0 if a.brand and a.brand == b.brand else 0.0,
        "qty": qty,
        "price_pen": price_pen,
    }


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def decide(prob: float, match_threshold: float = MATCH_THRESHOLD, review_threshold: float = REVIEW_THRESHOLD) -> str:
    if prob >= match_threshold:
        return "match"
    if prob >= review_threshold:
        return "review"
    return "no_match"


def score_pair(
    offer: ProductFeatures,
    candidate: ProductFeatures,
    price_ratio: float | None = None,
    weights: dict[str, float] | None = None,
    match_threshold: float = MATCH_THRESHOLD,
    review_threshold: float = REVIEW_THRESHOLD,
) -> MatchScore:
    """`price_ratio` = precio en ML / precio en el retailer (opcional)."""
    v = veto(offer, candidate)
    if v:
        return MatchScore(0.0, "no_match", (v,), veto=v)
    if offer.gtins and candidate.gtins and offer.gtins & candidate.gtins:
        return MatchScore(0.99, "match", ("mismo GTIN",))

    w = weights or DEFAULT_WEIGHTS
    f = feature_vector(offer, candidate, price_ratio)
    prob = _sigmoid(sum(w.get(k, 0.0) * f[k] for k in FEATURES))
    reasons = []
    if f["model"]:
        reasons.append("mismo código de modelo")
    if f["short"]:
        reasons.append("mismo modelo corto")
    if f["brand"]:
        reasons.append("misma marca")
    reasons.append(f"similitud de texto {f['char3']:.0%}")
    if f["price_pen"] > 0:
        reasons.append(f"relación de precios atípica ({price_ratio:.1f}×)")
    return MatchScore(prob, decide(prob, match_threshold, review_threshold), tuple(reasons), features=f)


# ── Calibración con pares etiquetados ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class LabeledPair:
    offer: ProductFeatures
    candidate: ProductFeatures
    label: bool
    price_ratio: float | None = None


def calibrate(
    pairs: Sequence[LabeledPair],
    l2: float = 0.5,
    lr: float = 0.3,
    epochs: int = 800,
    init: dict[str, float] | None = None,
) -> dict[str, float]:
    """Regresión logística (descenso de gradiente, L2 hacia los pesos por defecto) sobre los
    pares que NO caen en un veto. El prior de juicio experto regulariza: con pocos pares los
    pesos casi no se mueven, con muchos manda la evidencia."""
    prior = dict(init or DEFAULT_WEIGHTS)
    data = [
        (feature_vector(p.offer, p.candidate, p.price_ratio), 1.0 if p.label else 0.0)
        for p in pairs
        if veto(p.offer, p.candidate) is None and not (p.offer.gtins & p.candidate.gtins)
    ]
    w = dict(prior)
    if not data:
        return w
    n = len(data)
    for _ in range(epochs):
        grad = dict.fromkeys(FEATURES, 0.0)
        for f, y in data:
            err = _sigmoid(sum(w[k] * f[k] for k in FEATURES)) - y
            for k in FEATURES:
                grad[k] += err * f[k]
        for k in FEATURES:
            w[k] -= lr * (grad[k] / n + l2 * (w[k] - prior[k]) / n)
    return w


@dataclass(frozen=True)
class EvalRow:
    threshold: float
    precision: float
    recall: float
    f1: float
    n_pred: int


def evaluate(
    pairs: Sequence[LabeledPair],
    weights: dict[str, float] | None = None,
    thresholds: Sequence[float] = (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95),
) -> list[EvalRow]:
    probs = [(score_pair(p.offer, p.candidate, p.price_ratio, weights).prob, p.label) for p in pairs]
    positives = sum(1 for _, y in probs if y)
    rows = []
    for t in thresholds:
        tp = sum(1 for pr, y in probs if pr >= t and y)
        fp = sum(1 for pr, y in probs if pr >= t and not y)
        prec = tp / (tp + fp) if tp + fp else 1.0
        rec = tp / positives if positives else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        rows.append(EvalRow(t, prec, rec, f1, tp + fp))
    return rows


def threshold_for_precision(rows: Sequence[EvalRow], target: float = 0.97) -> float | None:
    """Umbral más bajo (más recall) que mantiene la precisión objetivo."""
    ok = [r for r in rows if r.precision >= target and r.n_pred > 0]
    return min(ok, key=lambda r: r.threshold).threshold if ok else None
