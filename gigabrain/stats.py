"""Statistics to compare the modes (no external dependencies).

With several repetitions per project, each mode gets one mean per project. Two modes
are compared paired by project:

- Wilcoxon signed-rank test (two-sided, exact p for n <= 25)
- effect size: paired rank-biserial correlation (from -1 to 1);
  r > 0 means the first mode tends to be higher
"""

from __future__ import annotations

import math
from itertools import combinations

MODE_ORDER = ("council", "single_agent", "plain_llm")
COMPARED_METRICS = ["f1", "precision", "recall", "class", "subtype", "ears", "story", "gherkin",
                    "repetitions_avoided", "changes_captured", "changes_linked"]


def mean(values: list[float]) -> float | None:
    v = [x for x in values if x is not None]
    return sum(v) / len(v) if v else None


def stdev(values: list[float]) -> float | None:
    """Sample standard deviation."""
    v = [x for x in values if x is not None]
    if len(v) < 2:
        return 0.0 if v else None
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def _ranks(values: list[float]) -> list[float]:
    """Ranks 1..n with the average for ties."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def wilcoxon_paired(a: list[float], b: list[float]) -> dict:
    """Compare a and b paired. Zero differences are dropped (Wilcoxon's method)."""
    diffs = [x - y for x, y in zip(a, b) if x is not None and y is not None and abs(x - y) > 1e-12]
    n = len(diffs)
    if n == 0:
        return {"n": 0, "w_plus": 0.0, "w_minus": 0.0, "p": 1.0, "effect": 0.0}
    ranks = _ranks([abs(d) for d in diffs])
    w_plus = sum(r for r, d in zip(ranks, diffs) if d > 0)
    w_minus = sum(r for r, d in zip(ranks, diffs) if d < 0)
    total = n * (n + 1) / 2

    if n <= 25:
        # Exact distribution of W+ under H0 (equally likely signs); doubled ranks become integers.
        integers = [round(r * 2) for r in ranks]
        dist = {0: 1}
        for r in integers:
            new = dict(dist)
            for s, c in dist.items():
                new[s + r] = new.get(s + r, 0) + c
            dist = new
        w = round(min(w_plus, w_minus) * 2)
        p = min(1.0, 2 * sum(c for s, c in dist.items() if s <= w) / 2 ** n)
    else:
        m = total / 2
        sd = math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
        z = (min(w_plus, w_minus) - m) / sd
        p = min(1.0, math.erfc(-z / math.sqrt(2)))
    return {"n": n, "w_plus": w_plus, "w_minus": w_minus, "p": round(p, 4), "effect": round((w_plus - w_minus) / total, 3)}


def summarize(rows: list[dict]) -> dict:
    """rows: one per (project, mode, repetition). Returns means per mode and paired comparisons."""
    per_project: dict[tuple, dict[str, list]] = {}
    for r in rows:
        key = (r["project"], r["mode"])
        for m in COMPARED_METRICS:
            if r.get(m) is not None:
                per_project.setdefault(key, {}).setdefault(m, []).append(r[m])
    means = {key: {m: mean(v) for m, v in ms.items()} for key, ms in per_project.items()}
    modes = sorted({mode for _, mode in means}, key=lambda m: MODE_ORDER.index(m) if m in MODE_ORDER else 9)
    projects = sorted({p for p, _ in means})

    per_mode = {}
    for mode in modes:
        per_mode[mode] = {}
        for m in COMPARED_METRICS:
            values = [means[(p, mode)].get(m) for p in projects if (p, mode) in means]
            values = [v for v in values if v is not None]
            within = [stdev(per_project[(p, mode)].get(m, [])) for p in projects if (p, mode) in per_project]
            if values:
                per_mode[mode][m] = {
                    "mean": round(mean(values), 4),
                    "stdev_between_projects": round(stdev(values), 4),
                    "stdev_between_repetitions": round(mean([d for d in within if d is not None]) or 0.0, 4),
                    "projects": len(values),
                }

    comparisons = []
    for a, b in combinations(modes, 2):
        common = [p for p in projects if (p, a) in means and (p, b) in means]
        for m in COMPARED_METRICS:
            pairs = [(means[(p, a)].get(m), means[(p, b)].get(m)) for p in common]
            pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
            if len(pairs) < 2:
                continue
            test = wilcoxon_paired([x for x, _ in pairs], [y for _, y in pairs])
            comparisons.append({"a": a, "b": b, "metric": m, "projects": len(pairs),
                                "mean_a": round(mean([x for x, _ in pairs]), 4), "mean_b": round(mean([y for _, y in pairs]), 4), **test})
    return {"per_mode": per_mode, "comparisons": comparisons, "projects": projects, "modes": modes}
