"""Processes a meeting transcript one utterance at a time (after-meeting batch mode).

    for each stakeholder utterance:
      Meeting ──utterance──▶ Twin drafts the requirements of that utterance
                               │
                               ├─ single_agent (baseline): saves the draft directly
                               │
                               └─ council, one draft at a time:
                                    Critic scores the draft (a competency failed: back to the Twin)
                                    Twin ──query──▶ Leader ──▶ expert of the topic + quality expert
                                      ▲                              │ recommend: keep, fix,
                                      └──── recommendation ◀─────────┘ discard, links
                                    Critic scores what the council added (failed: back to the Leader)
                                    Critic scores the draft together with the repository ──report──▶ Twin
                                    Twin decides (final vote) ──▶ Requirements Repository
                                    the expert learns right away (notes the requirement)
      ──▶ next utterance
    ──▶ Critic scores the whole repository

There is no PO in a meeting processed in batch: the Digital Twin, who represents the PO
in the Deliberative Council, gives the final vote. The Consultative Council only recommends,
and the Critic only scores: what does not pass goes back to whoever wrote it, and what still
does not pass after that reaches the Twin with the report (or is discarded, in strict mode).

In the baselines (single_agent, plain_llm) the Critic scores what is saved and sends nothing back.

A third mode, plain_llm, sends the whole meeting in one request with a minimal prompt.
"""

from __future__ import annotations

import json
import os

from . import evaluation, evaluation_meetings, requirement_format, scenarios
from .agents import Context, DigitalTwin, Expert, Leader
from .database import slug
from .messages import LEADER, REPOSITORY, SYSTEM, TWIN, Message

MODES = ("council", "single_agent", "plain_llm")
DECIDED_BY = {
    "council": "twin (final vote after the council)",
    "single_agent": "twin (single agent, no council)",
    "plain_llm": "plain llm (baseline, no council)",
}
REQUIREMENT_FIELDS = ("title", "statement", "ears_pattern", "story", "acceptance_criteria", "class", "subtype", "kind")


def _record(ctx: Context, sender: str, recipient: str, kind: str, content: dict) -> None:
    ctx.recorder.record(Message(sender=sender, recipient=recipient, kind=kind, content=content, conversation_id=ctx.conversation_id))


def _save(ctx: Context, project: dict, draft: dict, final: dict, recommendation: dict | None, mode: str,
          check: dict | None = None) -> tuple[dict, dict]:
    content = {k: draft.get(k) for k in REQUIREMENT_FIELDS}
    content.update({"statement": final["statement"], "class": final["class"], "subtype": final["subtype"],
                    "topics": [draft["topic"]], "turns": draft["turns"]})
    content = {**requirement_format.normalize(content), "topics": [draft["topic"]], "turns": draft["turns"]}
    sources = [{"kind": "conversation", "ref": ctx.conversation_id}]
    sources += [{"kind": "turn", "ref": t, "project": project["id"]} for t in draft["turns"]]
    if recommendation and recommendation.get("expert_id"):
        sources.append({"kind": "expert", "ref": recommendation["expert_id"], "version": recommendation.get("knowledge_version")})
    if check:  # which report of the Critic this requirement was saved with
        sources.append({"kind": "validation", "ref": check["report_id"], "verdict": check["verdict"]})
    result = ctx.db.save_requirement(content, ctx.conversation_id, reason=final.get("motive") or "drafted from the transcript",
                                     links=final.get("links"), sources=sources)
    _record(ctx, TWIN, REPOSITORY, "requirement_saved", {**result, "decided_by": DECIDED_BY[mode]})
    for rid in result["under_review"]:
        _record(ctx, REPOSITORY, TWIN, "requirement_under_review", {"requirement_id": rid, "because_of": result["requirement_id"]})
    return result, content


def _already_saved(ctx: Context) -> list[dict]:
    """Everything already in the Repository (including previous meetings)."""
    return [{"id": r["id"], "statement": r["content"].get("statement") or r["title"]} for r in ctx.db.list_requirements(only_active=True)]


def process(ctx: Context, project: dict, mode: str = "council") -> dict:
    """Process one meeting. mode: "council", "single_agent" or "plain_llm"."""
    ctx.mode = "after_meeting"
    ctx.conversation_id = ctx.db.start_conversation(f"meeting_{mode}")
    _record(ctx, SYSTEM, TWIN, "transcript", {"project": project["id"], "title": project["title"], "turns": len(project["turns"])})
    language = project.get("language", "en")
    utterances = {t["id"]: t["text"] for t in project["turns"]}
    twin = DigitalTwin(ctx)
    critic = ctx.critic
    if critic:  # the baselines are scored, never sent back: they must stay what they are
        critic.observe_only = mode != "council"
    predicted, discarded = [], []

    def keep(draft: dict, final: dict, recommendation: dict | None, guidance: dict | None = None) -> None:
        check = None
        if final["save"] and critic:
            check = critic.for_saving(draft, recommendation, final, project, guidance)
            if critic.closes_the_gate(check):
                final = {**final, "save": False, "motive": "validation: did not pass " + ", ".join(check["failed_dimensions"])}
        if not final["save"]:
            discarded.append({**draft, "recommendation": recommendation, "motive": final["motive"]})
            return
        result, content = _save(ctx, project, draft, final, recommendation, mode, check)
        predicted.append({"id": result["requirement_id"], "text": final["statement"], "class": final["class"],
                          "subtype": final["subtype"], "turns": draft["turns"], "topic": draft["topic"],
                          "quality": requirement_format.check(content)})
        if check:
            predicted[-1]["validation"] = {k: check[k] for k in ("report_id", "verdict", "passed", "score", "scores", "failed_dimensions", "rounds")}
        if mode == "council":
            exp = Expert.load(ctx, slug(draft["topic"]))
            if exp:  # the expert of the topic learns right away
                exp.note_requirement({"id": result["requirement_id"], "statement": final["statement"], "class": final["class"],
                                      "reason": final.get("motive")}, result["links"], result["under_review"])

    if mode == "plain_llm":
        for draft in twin.extract_all_at_once(project):
            keep(draft, {**draft, "save": True, "links": [], "motive": "extracted by the plain llm"}, None)
    else:
        leader = Leader(ctx) if mode == "council" else None
        total = 0
        for i, turn in enumerate(project["turns"]):
            if turn["speaker"] == "Analyst":  # the Analyst only leads; it becomes context for the next utterances
                continue
            _record(ctx, SYSTEM, TWIN, "utterance", {"turn": turn["id"], "speaker": turn["speaker"], "text": turn["text"]})
            drafts = twin.draft(project, i, _already_saved(ctx), total)
            total += len(drafts)
            for draft in drafts:  # each draft goes to the end before the next one
                if leader:
                    cited = {t: utterances[t] for t in draft["turns"] if t in utterances}
                    if critic:  # gate of the Twin: the five item competencies of its draft
                        draft = critic.gate_draft(twin, draft, project)
                    twin.send(LEADER, "query", {"question": f"draft from {turn['id']}: {draft['statement'][:100]}", "draft": draft})
                    recommendation = leader.consult_draft(draft, cited, language)
                    guidance = None
                    if critic:  # gate of the Consultative Council, then everything together for the vote
                        recommendation = critic.gate_recommendation(leader, draft, recommendation, cited, project)
                        guidance = critic.guide(draft, recommendation, project)
                    keep(draft, twin.decide(draft, recommendation, cited, language, guidance), recommendation, guidance)
                else:
                    keep(draft, {**draft, "save": True, "links": [], "motive": "drafted from the transcript"}, None)

    # Everything together, at the end: the requirements of the repository as one specification.
    repository = critic.review_repository() if critic else None
    ctx.db.end_conversation(ctx.conversation_id, "approved")
    return {"conversation_id": ctx.conversation_id, "predicted": predicted, "discarded": discarded, "validation": repository}


def _process_meetings(ctx: Context, project: dict, mode: str) -> dict:
    """A project with several meetings: one after the other, over the SAME repository.

    plain_llm is the exception: the baseline sends the whole input in one request.
    """
    meetings = project.get("meetings") or []
    if len(meetings) <= 1 or mode == "plain_llm":
        return process(ctx, project, mode)
    predicted, discarded, conversations, repository = [], [], [], None
    for k, m in enumerate(meetings, start=1):
        part = {**project, "title": f"{project['title']} (meeting {k}/{len(meetings)})", "turns": m["turns"]}
        r = process(ctx, part, mode)
        predicted += r["predicted"]
        discarded += r["discarded"]
        conversations.append(r["conversation_id"])
        repository = r["validation"]  # the repository as the last meeting left it
    return {"conversation_id": conversations[-1], "conversations": conversations, "predicted": predicted,
            "discarded": discarded, "validation": repository}


def _structure_metrics(predicted: list[dict]) -> dict:
    """Share of saved requirements with an EARS statement, a full story, Gherkin criteria and no vague terms."""
    n = len(predicted)
    rate = lambda f: round(sum(1 for p in predicted if f(p["quality"])) / n, 4) if n else None
    return {
        "ears": rate(lambda q: q["ears"]),
        "story": rate(lambda q: q["story_complete"]),
        "gherkin": rate(lambda q: q["criteria_gherkin"]),
        "not_vague": rate(lambda q: not q["vague_terms"]),
    }


def _validation_metrics(predicted: list[dict]) -> dict:
    """Share of saved requirements that pass the five item competencies, and each competency on its own."""
    scored = [p["validation"] for p in predicted if p.get("validation")]
    if not scored:
        return {}
    rate = lambda f: round(sum(1 for v in scored if f(v)) / len(scored), 4)
    metrics = {"valid": rate(lambda v: v["passed"])}
    for dimension in scored[0]["scores"]:
        metrics[f"valid_{dimension}"] = rate(lambda v, d=dimension: d not in v["failed_dimensions"])
    return metrics


def _report(ctx: Context, project: dict, mode: str, base: dict) -> dict:
    base["metrics"].update(_structure_metrics(base["predicted"]))
    base["metrics"].update(_validation_metrics(base["predicted"]))
    if "negatives" in project:  # gold standard schema 2.0
        base.update(evaluation_meetings.evaluate(base, project))
        base["metrics"].update(base.pop("metrics_meetings"))
    return {**base, "project": project["id"], "title": project["title"], "mode": mode,
            "provider": getattr(ctx.provider, "name", "?"), "gold": project["gold"]}


def _write(folder: str, report: dict) -> dict:
    # Without validation, the file is the one the system always wrote.
    written = {k: v for k, v in report.items() if k != "validation" or v is not None}
    with open(os.path.join(folder, "evaluation.json"), "w", encoding="utf-8") as f:
        json.dump(written, f, ensure_ascii=False, indent=1)
    return report


def process_and_evaluate(ctx: Context, project: dict, output_folder: str, mode: str = "council") -> dict:
    result = _process_meetings(ctx, project, mode)
    report = evaluation.evaluate(result["predicted"], project["gold"], project["turns"])
    report.update({"scenario": "meeting", "conversation_id": result["conversation_id"],
                   "predicted": result["predicted"], "discarded": result["discarded"], "validation": result["validation"]})
    if "conversations" in result:
        report["conversations"] = result["conversations"]
    return _write(output_folder, _report(ctx, project, mode, report))


def process_sessions_and_evaluate(ctx: Context, project: dict, scenario: dict, output_folder: str, mode: str = "council") -> dict:
    """Several meetings in sequence over the SAME repository."""
    predicted, discarded, conversations, repository = [], [], [], None
    n = len(scenario["sessions"])
    for k, session in enumerate(scenario["sessions"], start=1):
        part = {**project, "title": f"{project['title']} (session {k}/{n})", "turns": session["turns"]}
        r = process(ctx, part, mode)
        predicted += r["predicted"]
        discarded += r["discarded"]
        conversations.append(r["conversation_id"])
        repository = r["validation"]  # the repository as the last session left it
    report = scenarios.evaluate(ctx.db, project, scenario, predicted)
    report.update({"scenario": "sessions", "conversations": conversations, "predicted": predicted, "discarded": discarded,
                   "validation": repository})
    return _write(output_folder, _report(ctx, project, mode, report))
