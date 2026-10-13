"""Manual annotation of the links between requirements.

The dataset's gold standard says nothing about links (refines, depends_on...). To measure
whether they are right, two people review a sample:

1. export:  draws N links from the council runs and writes a CSV (opens in Excel or
            LibreOffice) with the two requirements side by side
2. annotate: each person fills in their own column with "c" (correct) or "i" (incorrect),
            without looking at the other column
3. kappa:   computes the agreement between them (Cohen's kappa) and the precision of the
            links according to each annotator
"""

from __future__ import annotations

import csv
import glob
import json
import os
import random
import sqlite3

COLUMNS = ["id", "run", "source", "source_statement", "source_utterance", "kind", "target", "target_statement",
           "target_utterance", "system_reason", "annotator_1", "annotator_2", "notes"]
CORRECT = {"c", "correct", "1", "y", "yes", "true"}
INCORRECT = {"i", "incorrect", "0", "n", "no", "false"}


def _links_of_folder(folder: str) -> list[dict]:
    req = sqlite3.connect(os.path.join(folder, "requirements.db"))
    log = sqlite3.connect(os.path.join(folder, "log.db"))
    try:
        utterances = {}
        for (content,) in log.execute("SELECT content FROM event WHERE kind = 'utterance'"):
            c = json.loads(content)
            utterances[c["turn"]] = f"{c['speaker']}: {c['text']}"
        statements, turns = {}, {}
        for rid, content in req.execute(
            "SELECT v.requirement_id, v.content FROM requirement_version v JOIN requirement r "
            "ON r.id = v.requirement_id AND r.current_version = v.version"
        ):
            c = json.loads(content)
            statements[rid] = c.get("statement") or c.get("title")
            turns[rid] = c.get("turns", [])
        utterance = lambda rid: " | ".join(f"[{t}] {utterances.get(t, '')}" for t in turns.get(rid, []))
        return [
            {"source": s, "kind": k, "target": t, "system_reason": r or "",
             "source_statement": statements.get(s, ""), "target_statement": statements.get(t, ""),
             "source_utterance": utterance(s), "target_utterance": utterance(t)}
            for s, k, t, r in req.execute("SELECT source_id, kind, target_id, reason FROM requirement_link ORDER BY id")
        ]
    finally:
        req.close()
        log.close()


def export(root: str, output: str, sample: int = 60, seed: int = 7, provider: str = "*", scenario: str = "*") -> int:
    """Draw links from the council runs and write the CSV for annotation."""
    pattern = os.path.join(root, "data-eval", provider, scenario, "*", "council", "rep-*")
    links = []
    for folder in sorted(glob.glob(pattern)):
        if not os.path.exists(os.path.join(folder, "requirements.db")):
            continue
        run = os.path.relpath(folder, os.path.join(root, "data-eval")).replace("\\", "/")
        links += [{"run": run, **l} for l in _links_of_folder(folder)]
    random.Random(seed).shuffle(links)
    chosen = links[:sample]
    # utf-8-sig and ";" open directly in Excel with European/Brazilian regional settings.
    with open(output, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, delimiter=";")
        writer.writeheader()
        for i, l in enumerate(chosen, start=1):
            writer.writerow({"id": i, **l, "annotator_1": "", "annotator_2": "", "notes": ""})
    return len(chosen)


def _label(value: str) -> str | None:
    v = (value or "").strip().lower()
    return "c" if v in CORRECT else "i" if v in INCORRECT else None


def kappa(path: str) -> dict:
    """Cohen's kappa between annotator_1 and annotator_2, and the link precision per annotator."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        head = f.read(2048)
        f.seek(0)
        rows = list(csv.DictReader(f, delimiter=";" if head.count(";") >= head.count(",") else ","))
    pairs = [(_label(r.get("annotator_1")), _label(r.get("annotator_2"))) for r in rows]
    pairs = [(a, b) for a, b in pairs if a and b]
    n = len(pairs)
    if not n:
        return {"annotated": 0}
    agreement = sum(a == b for a, b in pairs) / n
    p1 = sum(a == "c" for a, _ in pairs) / n
    p2 = sum(b == "c" for _, b in pairs) / n
    expected = p1 * p2 + (1 - p1) * (1 - p2)
    k = 1.0 if expected == 1 else (agreement - expected) / (1 - expected)
    return {
        "annotated": n,
        "agreement": round(agreement, 4),
        "kappa": round(k, 4),
        "precision_annotator_1": round(p1, 4),
        "precision_annotator_2": round(p2, 4),
        "precision_consensus": round(sum(a == b == "c" for a, b in pairs) / n, 4),
    }
