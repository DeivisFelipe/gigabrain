"""Scenario with several meetings, to measure what the original dataset does not.

The dataset has each project as ONE meeting. Here it becomes a sequence of sessions
(2 or 3 meetings in a row over the same repository) and, in the later sessions,
utterances with a known answer are injected:

- repetition: a stakeholder repeats a requirement said in an earlier session
              ("As I said last time, ..."). The right thing is NOT to save it again.
- change:     a stakeholder changes a requirement of an earlier session (another number,
              or a new restriction). The right thing is to save the new version and LINK
              it to the original one (supersedes, conflicts_with or refines).

Generation is deterministic (fixed seed), without an LLM, so it is reproducible.
Injected utterances use ids T9001, T9002... and are left out of precision/recall
against the original gold standard.
"""

from __future__ import annotations

import random
import re

from .database import Database
from .evaluation import evaluate as evaluate_extraction, similarity

REPETITION_PHRASES = [
    "As I said in the last meeting,",
    "Just to repeat what we agreed before,",
    "Like I mentioned last time,",
    "Again, as we discussed,",
]
CHANGE_PHRASES = [
    "Actually, we need to change something we agreed before:",
    "We talked to the team and want to change this:",
    "Change of plan on an earlier point:",
]
CHANGE_LINKS = ("supersedes", "conflicts_with", "refines")
FIRST_INJECTED_ID = 9001


def _change(text: str, rng: random.Random) -> str:
    """Change the requirement in a verifiable way: another number, or a new restriction."""
    number = re.search(r"\b\d+\b", text)
    if number:
        new = int(number.group()) * 2 or 1
        return text[:number.start()] + str(new) + text[number.end():]
    return text.rstrip(". ") + rng.choice([
        ", but only for administrators.",
        ", but only after the user confirms it.",
        ", and this must also be logged.",
    ])


def generate_sessions(project: dict, n_sessions: int = 3, fraction: float = 0.15, seed: int = 42) -> dict:
    turns = project["turns"]
    rng = random.Random(f"{project['id']}-{seed}")

    # Cut at the Analyst utterances (start of a topic) closest to equal parts.
    analyst = [i for i, t in enumerate(turns) if t["speaker"] == "Analyst" and i > 0]
    cuts = []
    for k in range(1, n_sessions):
        target = round(len(turns) * k / n_sessions)
        cuts.append(min(analyst, key=lambda i: abs(i - target)) if analyst else target)
    cuts = sorted(set(c for c in cuts if 0 < c < len(turns)))
    bounds = [0, *cuts, len(turns)]
    sessions = [{"turns": list(turns[a:b])} for a, b in zip(bounds, bounds[1:])]
    session_of_turn = {t["id"]: k for k, s in enumerate(sessions) for t in s["turns"]}

    # Gold requirements that can be repeated/changed in a later session.
    candidates = [g for g in project["gold"] if g["turns"] and session_of_turn.get(g["turns"][0], len(sessions)) < len(sessions) - 1]
    rng.shuffle(candidates)
    k = max(1, round(len(project["gold"]) * fraction)) if candidates else 0
    to_repeat, to_change = candidates[:k], candidates[k:2 * k]

    injections, next_id = [], FIRST_INJECTED_ID
    speaker = {t["id"]: t["speaker"] for t in turns}
    for kind, items in (("repetition", to_repeat), ("change", to_change)):
        for g in items:
            origin = session_of_turn[g["turns"][0]]
            target_session = rng.randint(origin + 1, len(sessions) - 1)
            text = g["text"] if kind == "repetition" else _change(g["text"], rng)
            phrase = rng.choice(REPETITION_PHRASES if kind == "repetition" else CHANGE_PHRASES)
            turn = {"id": f"T{next_id}", "speaker": speaker.get(g["turns"][0], "Client"), "text": f"{phrase} {text}", "context": False}
            target_turns = sessions[target_session]["turns"]
            target_turns.insert(rng.randint(1, len(target_turns)), turn)
            injections.append({"turn": turn["id"], "kind": kind, "original": g["id"], "session": target_session + 1,
                               "original_text": g["text"], "injected_text": text})
            next_id += 1

    return {"project": project["id"], "seed": seed, "sessions": sessions, "injections": injections}


def evaluate(db: Database, project: dict, scenario: dict, predicted: list[dict]) -> dict:
    """Extraction against the gold standard (without injected utterances) + repetitions avoided + changes linked."""
    injected = {i["turn"]: i for i in scenario["injections"]}
    turns = [t for s in scenario["sessions"] for t in s["turns"]]
    base = [p for p in predicted if not set(p["turns"]) & injected.keys()]
    report = evaluate_extraction(base, project["gold"], turns)
    requirement_of_gold = {x["gold"]: x["predicted"] for x in report["pairs"]}
    links = db.list_links()

    details = []
    for inj in scenario["injections"]:
        saved = [p for p in predicted if inj["turn"] in p["turns"]]
        original = requirement_of_gold.get(inj["original"])
        item = {**inj, "saved": [p["id"] for p in saved], "original_requirement": original}
        if inj["kind"] == "repetition":
            # Avoided if nothing saved from that utterance repeats the original requirement.
            item["ok"] = not any(similarity(p["text"], inj["original_text"]) >= 0.5 for p in saved)
        else:
            item["captured"] = bool(saved)
            item["original_extracted"] = original is not None
            item["ok"] = bool(original) and any(
                l["source_id"] in item["saved"] and l["target_id"] == original and l["kind"] in CHANGE_LINKS for l in links
            )
        details.append(item)

    rep = [d for d in details if d["kind"] == "repetition"]
    chg = [d for d in details if d["kind"] == "change"]
    valid = [d for d in chg if d["original_extracted"]]
    rate = lambda a, b: round(a / b, 4) if b else None
    report["metrics"].update({
        "repetitions_avoided": rate(sum(d["ok"] for d in rep), len(rep)),
        "changes_captured": rate(sum(d["captured"] for d in chg), len(chg)),
        "changes_linked": rate(sum(d["ok"] for d in valid), len(valid)),
    })
    report["injections"] = details
    return report
