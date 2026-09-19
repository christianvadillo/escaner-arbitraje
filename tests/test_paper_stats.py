import pytest

from escaner.paper.stats import (
    PaperPosition,
    block_bootstrap_mean,
    kaplan_meier,
    median_survival,
    summarize,
    survival_at,
)


def test_kaplan_meier_textbook():
    curve = kaplan_meier([1, 2, 2, 3, 4], [True, True, False, True, False])
    assert [round(p.survival, 3) for p in curve] == [0.8, 0.6, 0.3]
    assert median_survival(curve) == 3
    assert survival_at(curve, 2.5) == pytest.approx(0.6)


def test_block_bootstrap_brackets_mean():
    vals = [10, 12, 9, -3, -1, 15, 8, 7]
    blocks = ["d1", "d1", "d2", "d2", "d3", "d3", "d4", "d4"]
    m, lo, hi = block_bootstrap_mean(vals, blocks, n_boot=500)
    assert lo <= m <= hi


def test_summary_verdicts():
    few = [PaperPosition(str(i), "2026-09-19", 1000, 200, 150, 30, True) for i in range(5)]
    assert summarize(few).verdict.startswith("INSUFICIENTE")
    many = [
        PaperPosition(str(i), f"2026-09-{1 + i % 15:02d}", 1000, 200, 120 + (i % 7) * 10, 20 + i, i % 3 != 0)
        for i in range(60)
    ]
    s = summarize(many)
    assert s.verdict.startswith("BRECHA SOSTENIDA")
    assert 0 < s.decay < 1
