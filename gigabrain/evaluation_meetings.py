"""What the gold standard schema 2.0 adds to evaluation.py, which is left as it is.

evaluation.evaluate pairs predictions with what should come out (precision, recall, F1). Here,
with the same similarity and the same threshold, come the measures of the dataset's README
(EntradasGigabrain, section 5) that GigaBrain can answer:

- distractor_hits, rejected_hits, omitted_hits   predictions without a pair that match a losing
                                                 proposal, a withdrawn requirement or one nobody
                                                 discussed
- recall_by_status                               where the recall is lost
- detail_hallucination                           among the ambiguous or incomplete requirements
                                                 found, how many bring the detail removed from
                                                 the meetings (e.g. the exact number nobody said)

The issues of the gold standard are not measured: GigaBrain has no output for them yet.
"""

from __future__ import annotations

from .evaluation import THRESHOLD, similarity, words

REMOVED_DETAIL_STATUS = ("ambiguous", "incomplete")


def _negative_hits(unmatched: list[dict], negatives: list[dict]) -> dict[str, list[dict]]:
    """Pairs (prediction without a pair, negative item), from the most similar, one to one."""
    candidates = []
    for i, p in enumerate(unmatched):
        for j, n in enumerate(negatives):
            s = similarity(p["text"], n["text"])
            if s >= THRESHOLD:
                candidates.append((s, i, j))
    candidates.sort(reverse=True)
    used_p, used_n, hits = set(), set(), {"distractor": [], "rejected": [], "omitted": []}
    for s, i, j in candidates:
        if i in used_p or j in used_n:
            continue
        used_p.add(i)
        used_n.add(j)
        hits.setdefault(negatives[j]["kind"], []).append(
            {"predicted": unmatched[i]["id"], "negative": negatives[j]["id"], "similarity": round(s, 3)})
    return hits


def _removed_detail(gold: dict) -> str | None:
    for phenomenon in gold.get("phenomena") or []:
        if phenomenon.get("type") in ("ambiguity", "incompleteness"):
            details = phenomenon.get("details") or {}
            return details.get("removed") or details.get("missing")
    return None


def evaluate(report: dict, project: dict) -> dict:
    """`report` comes from evaluation.evaluate, with "predicted"; returns what to add to it."""
    by_id = {p["id"]: p for p in report["predicted"]}
    gold_by_id = {g["id"]: g for g in project["gold"]}
    unmatched = [by_id[i] for i in report["false_positives"] if i in by_id]
    hits = _negative_hits(unmatched, project["negatives"])

    found: dict[str, int] = {}
    total: dict[str, int] = {}
    for g in project["gold"]:
        total[g.get("status")] = total.get(g.get("status"), 0) + 1
    for pair in report["pairs"]:
        status = gold_by_id[pair["gold"]].get("status")
        found[status] = found.get(status, 0) + 1

    said = words(" ".join(t["text"] for t in project["turns"]))
    with_detail, invented = 0, []
    for pair in report["pairs"]:
        gold = gold_by_id[pair["gold"]]
        removed = _removed_detail(gold) if gold.get("status") in REMOVED_DETAIL_STATUS else None
        if not removed:
            continue
        with_detail += 1
        if (words(removed) - said) & words(by_id[pair["predicted"]]["text"]):
            invented.append(pair["predicted"])

    return {
        "metrics_meetings": {
            "distractor_hits": len(hits["distractor"]),
            "rejected_hits": len(hits["rejected"]),
            "omitted_hits": len(hits["omitted"]),
            "detail_hallucination": round(len(invented) / with_detail, 4) if with_detail else None,
        },
        "negative_hits": hits,
        "recall_by_status": {s: round(found.get(s, 0) / n, 4) for s, n in sorted(total.items(), key=lambda x: str(x[0]))},
        "detail_hallucinated": invented,
        "variant": project.get("variant"),
    }
