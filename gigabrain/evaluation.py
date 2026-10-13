"""Compares the requirements GigaBrain extracted with the dataset's gold standard.

Matching: each predicted requirement is compared with each gold requirement by the
overlap of content words (Dice coefficient, ignoring stopwords). Pairs are chosen from
the most similar to the least, one to one, while the similarity is >= THRESHOLD.

Metrics:
- precision    of the predicted ones, how many exist in the gold standard
- recall       of the gold standard, how many were found
- f1           harmonic mean of both
- class        FR/NFR accuracy among the pairs
- subtype      subtype accuracy among the pairs that are NFR in the gold standard
- trace        pairs where some cited turn is the turn where the requirement was said
- unsupported  predicted requirements that cite no turn, or whose cited turns share almost
               no words with the requirement (a sign of hallucination)
"""

from __future__ import annotations

import re

THRESHOLD = 0.5
SUPPORT_THRESHOLD = 0.3

STOPWORDS = set("""
a an the and or but if then else of to in on at by for with from as is are was were be been being
it its this that these those there here which who whom what when where why how all any each both
shall should must will would could can may might need needs needed have has had do does did done
not no nor so such than too very just also only own same other some more most into over under
about above below between through during before after again further once i we you he she they them
our your their my me us his her uh um uhm erm like know mean sort well okay ok yeah yes right
system ensure
""".split())


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOPWORDS and len(w) > 1}


def similarity(a: str, b: str) -> float:
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    return 2 * len(wa & wb) / (len(wa) + len(wb))


def _coverage(requirement: str, utterance: str) -> float:
    """How many of the requirement's words appear in the cited utterance."""
    wr = words(requirement)
    return len(wr & words(utterance)) / len(wr) if wr else 0.0


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def evaluate(predicted: list[dict], gold: list[dict], turns: list[dict]) -> dict:
    """predicted/gold: [{"id", "text", "class", "subtype", "turns"}]."""
    utterances = {t["id"]: t["text"] for t in turns}

    candidates = []
    for i, p in enumerate(predicted):
        for j, g in enumerate(gold):
            s = similarity(p["text"], g["text"])
            if s >= THRESHOLD:
                candidates.append((s, i, j))
    candidates.sort(reverse=True)

    used_p, used_g, pairs = set(), set(), []
    for s, i, j in candidates:
        if i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        p, g = predicted[i], gold[j]
        pairs.append({
            "predicted": p["id"],
            "gold": g["id"],
            "similarity": round(s, 3),
            "class_ok": p.get("class") == g["class"],
            "subtype_ok": (p.get("subtype") == g["subtype"]) if g["class"] == "NFR" else None,
            "trace_ok": bool(set(p.get("turns") or []) & set(g["turns"])),
        })

    unsupported = []
    for p in predicted:
        cited = " ".join(utterances.get(t, "") for t in p.get("turns") or [])
        if not cited or _coverage(p["text"], cited) < SUPPORT_THRESHOLD:
            unsupported.append(p["id"])

    nfr = [x for x in pairs if x["subtype_ok"] is not None]
    hits = len(pairs)
    precision = _ratio(hits, len(predicted))
    recall = _ratio(hits, len(gold))
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision and recall else 0.0
    return {
        "metrics": {
            "predicted": len(predicted),
            "gold": len(gold),
            "hits": hits,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "class": _ratio(sum(x["class_ok"] for x in pairs), hits),
            "subtype": _ratio(sum(x["subtype_ok"] for x in nfr), len(nfr)),
            "trace": _ratio(sum(x["trace_ok"] for x in pairs), hits),
            "unsupported": _ratio(len(unsupported), len(predicted)),
        },
        "pairs": pairs,
        "not_found": [g["id"] for j, g in enumerate(gold) if j not in used_g],
        "false_positives": [p["id"] for i, p in enumerate(predicted) if i not in used_p],
        "unsupported": unsupported,
        "parameters": {"similarity_threshold": THRESHOLD, "support_threshold": SUPPORT_THRESHOLD},
    }
