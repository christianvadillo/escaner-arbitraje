"""report.py — reporte de oportunidades (Markdown y CSV) y del papel (Markdown), para que el
operador pueda leer una corrida sin abrir la base de datos."""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

from escaner.models import Opportunity
from escaner.paper.stats import PaperSummary


def opportunities_markdown(opportunities: Sequence[Opportunity], top_n: int = 20) -> str:
    lines = [f"# Oportunidades detectadas ({len(opportunities)})", ""]
    if not opportunities:
        lines.append("Ninguna oportunidad cruzó los umbrales en esta corrida.")
        return "\n".join(lines)
    ranked = sorted(opportunities, key=lambda o: -o.profit.net_profit)[:top_n]
    for i, o in enumerate(ranked, 1):
        lines.append(f"## {i}. {o.offer.title}")
        lines.append(f"- Retailer: {o.offer.merchant} — ${o.offer.price:,.2f} — {o.offer.url}")
        lines.append(f"- ML: {o.listing.title} — {o.listing.permalink or o.listing.item_id}")
        lines.append(
            f"- Match: {o.match.decision} ({o.match.prob:.0%}) — {', '.join(o.match.reasons) or 'sin razones'}"
        )
        lines.append(f"- Peso usado: {o.weight_source}")
        lines.append("")
        lines.append("| Concepto | Monto |")
        lines.append("|---|---:|")
        for label, value in o.profit.lines():
            lines.append(f"| {label} | ${value:,.2f} |")
        lines.append("")
        lines.append(f"- ROI: {o.profit.roi:.1%} · Margen: {o.profit.margin:.1%}")
        if o.flags:
            lines.append(f"- Flags: {', '.join(o.flags)}")
        if o.profit.notes:
            lines.append(f"- Notas: {'; '.join(o.profit.notes)}")
        lines.append("")
    return "\n".join(lines)


def opportunities_csv(opportunities: Sequence[Opportunity]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "offer_title",
            "merchant",
            "purchase_price",
            "ml_item_id",
            "sale_price",
            "net_profit",
            "roi",
            "match_prob",
            "decision",
            "flags",
            "detected_at",
        ]
    )
    for o in opportunities:
        writer.writerow(
            [
                o.offer.title,
                o.offer.merchant,
                o.offer.price,
                o.listing.item_id,
                o.profit.sale_price,
                round(o.profit.net_profit, 2),
                round(o.profit.roi, 4),
                round(o.match.prob, 3),
                o.match.decision,
                "|".join(o.flags),
                o.detected_at.isoformat(),
            ]
        )
    return buf.getvalue()


def _fmt_hours(h: float | None) -> str:
    return "no calculable (>50% censurado)" if h is None else f"{h:.1f} h"


def paper_report_markdown(summary: PaperSummary) -> str:
    exp_m, exp_lo, exp_hi = summary.expectancy
    roi_m, roi_lo, roi_hi = summary.roi
    return "\n".join(
        [
            "# Reporte de papel",
            "",
            f"**Veredicto:** {summary.verdict}",
            "",
            f"- Posiciones: {summary.n} ({summary.n_marked} marcadas)",
            f"- Tasa de acierto: {summary.hit_rate:.1%}",
            f"- Utilidad esperada por posición: ${exp_m:,.2f} (IC90% [${exp_lo:,.2f}, ${exp_hi:,.2f}])",
            f"- ROI: {roi_m:.1%} (IC90% [{roi_lo:.1%}, {roi_hi:.1%}])",
            f"- Decaimiento (utilidad marcada / esperada): {summary.decay:.1%}",
            f"- Vida mediana de la oferta: {_fmt_hours(summary.median_deal_hours)}",
            f"- Sobrevive 24h: {summary.deal_alive_24h:.1%} · Sobrevive 72h: {summary.deal_alive_72h:.1%}",
            f"- Capital medio por posición: ${summary.capital_mean:,.2f}",
        ]
    )
