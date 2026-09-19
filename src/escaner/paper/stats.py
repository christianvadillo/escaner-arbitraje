"""stats.py — ¿la brecha existe después de costos? Estadística del papel antes que del dinero.

El giro del producto es usarlo uno mismo primero: cada oportunidad detectada abre una posición
de papel (compra hipotética al precio de liquidación) y se marca a mercado con el precio
competitivo de Mercado Libre de los días siguientes. Esto responde tres preguntas con
incertidumbre explícita, no con una anécdota:

  1. ¿Cuánto vive una liquidación? Kaplan–Meier sobre el tiempo hasta que la oferta desaparece
     o sube de precio. Las ofertas que siguen vivas al cortar el análisis están CENSURADAS
     (no sabemos cuándo mueren): tratarlas como muertas subestima la vida media.
  2. ¿Qué tan seguido la brecha sobrevive a costos? Tasa de acierto (utilidad > 0 marcada a
     mercado) y esperanza por posición.
  3. ¿Con qué intervalo? Bootstrap por BLOQUES de día de detección: las oportunidades de un
     mismo día comparten régimen (quincena, Buen Fin, liquidación de temporada) y no son
     independientes; remuestrear posiciones sueltas daría intervalos falsamente estrechos.
"""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SurvivalPoint:
    t: float
    at_risk: int
    events: int
    survival: float


def kaplan_meier(durations: Sequence[float], observed: Sequence[bool]) -> list[SurvivalPoint]:
    """Estimador producto-límite. `observed=False` = censurado (la oferta seguía viva)."""
    if len(durations) != len(observed):
        raise ValueError("durations y observed deben tener el mismo largo")
    data = sorted(zip(durations, observed, strict=True))
    n_at_risk = len(data)
    s = 1.0
    curve: list[SurvivalPoint] = []
    i = 0
    while i < len(data):
        t = data[i][0]
        events = censored = 0
        while i < len(data) and data[i][0] == t:
            if data[i][1]:
                events += 1
            else:
                censored += 1
            i += 1
        if events:
            s *= 1.0 - events / n_at_risk
            curve.append(SurvivalPoint(t=t, at_risk=n_at_risk, events=events, survival=s))
        n_at_risk -= events + censored
    return curve


def survival_at(curve: Sequence[SurvivalPoint], t: float) -> float:
    s = 1.0
    for p in curve:
        if p.t > t:
            break
        s = p.survival
    return s


def median_survival(curve: Sequence[SurvivalPoint]) -> float | None:
    """Primer t con S(t) ≤ 0.5; None si la curva nunca baja de 0.5 (más de la mitad censurada)."""
    for p in curve:
        if p.survival <= 0.5:
            return p.t
    return None


def block_bootstrap_mean(
    values: Sequence[float],
    blocks: Sequence[str],
    n_boot: int = 2000,
    alpha: float = 0.10,
    seed: int = 7,
) -> tuple[float, float, float]:
    """Media e IC (1−alpha) remuestreando bloques completos con reemplazo."""
    if not values:
        return (0.0, 0.0, 0.0)
    groups: dict[str, list[float]] = defaultdict(list)
    for v, b in zip(values, blocks, strict=True):
        groups[b].append(v)
    keys = list(groups)
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        sample: list[float] = []
        for _ in keys:
            sample.extend(groups[rng.choice(keys)])
        means.append(statistics.fmean(sample))
    means.sort()
    lo = means[int(alpha / 2 * (n_boot - 1))]
    hi = means[int((1 - alpha / 2) * (n_boot - 1))]
    return (statistics.fmean(values), lo, hi)


@dataclass(frozen=True)
class PaperPosition:
    """Una oportunidad seguida en papel. Montos en MXN."""

    position_id: str
    detection_day: str  # YYYY-MM-DD (bloque del bootstrap)
    capital: float  # compra + empaque
    expected_profit: float  # al detectar
    marked_profit: float | None  # utilidad re-calculada con el precio de ML al marcar
    deal_hours: float  # horas observadas de vida de la oferta de origen
    deal_ended: bool  # True = se vio morir; False = seguía viva (censurada)
    days_marked: float = 0.0


@dataclass(frozen=True)
class PaperSummary:
    n: int
    n_marked: int
    hit_rate: float  # fracción con utilidad marcada > 0
    expectancy: tuple[float, float, float]  # (media, lo, hi) de utilidad marcada
    roi: tuple[float, float, float]  # (media, lo, hi) de utilidad/capital
    decay: float  # utilidad marcada / esperada (1 = la brecha se sostuvo)
    median_deal_hours: float | None
    deal_alive_24h: float
    deal_alive_72h: float
    capital_mean: float
    verdict: str


def summarize(positions: Sequence[PaperPosition], min_positions: int = 30, min_days: int = 10) -> PaperSummary:
    marked = [p for p in positions if p.marked_profit is not None]
    curve = kaplan_meier([p.deal_hours for p in positions], [p.deal_ended for p in positions])
    if not marked:
        return PaperSummary(
            n=len(positions),
            n_marked=0,
            hit_rate=0.0,
            expectancy=(0.0, 0.0, 0.0),
            roi=(0.0, 0.0, 0.0),
            decay=0.0,
            median_deal_hours=median_survival(curve),
            deal_alive_24h=survival_at(curve, 24),
            deal_alive_72h=survival_at(curve, 72),
            capital_mean=statistics.fmean([p.capital for p in positions]) if positions else 0.0,
            verdict="SIN DATOS: aún no hay posiciones marcadas a mercado",
        )
    profits = [p.marked_profit for p in marked]  # type: ignore[misc]
    rois = [p.marked_profit / p.capital if p.capital else 0.0 for p in marked]  # type: ignore[operator]
    blocks = [p.detection_day for p in marked]
    exp = block_bootstrap_mean(profits, blocks)
    roi = block_bootstrap_mean(rois, blocks)
    expected_total = sum(p.expected_profit for p in marked)
    decay = sum(profits) / expected_total if expected_total else 0.0
    n_days = len(set(blocks))
    if len(marked) < min_positions or n_days < min_days:
        verdict = f"INSUFICIENTE: {len(marked)} posiciones en {n_days} días (mín. {min_positions} en {min_days})"
    elif exp[1] > 0:
        verdict = "BRECHA SOSTENIDA: el IC90% de la utilidad por posición está sobre cero"
    elif exp[2] < 0:
        verdict = "SIN EDGE: el IC90% de la utilidad por posición está bajo cero"
    else:
        verdict = "INDETERMINADO: el IC90% cruza cero; seguir acumulando"
    return PaperSummary(
        n=len(positions),
        n_marked=len(marked),
        hit_rate=sum(1 for x in profits if x > 0) / len(profits),
        expectancy=exp,
        roi=roi,
        decay=decay,
        median_deal_hours=median_survival(curve),
        deal_alive_24h=survival_at(curve, 24),
        deal_alive_72h=survival_at(curve, 72),
        capital_mean=statistics.fmean([p.capital for p in marked]),
        verdict=verdict,
    )
