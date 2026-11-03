"""Critic: the agent that validates what the other agents produce.

It never writes a requirement. It scores them, and it reads the sources through
its own path (the transcript, the PO messages in the log), never through the
agent that wrote the requirement. That is what lets it check a declared origin
instead of believing it.

Two things happen to everything an agent produces:

1. **The gate of each agent.** Every requirement an agent writes (a proposal or
   a draft of the Twin, the version the Consultative Council recommends) gets
   one score for each of the five item competencies: conforming, unambiguous,
   verifiable, correct (grounded in its source) and understandable. If a
   competency does not pass, the artefact goes back to the agent that wrote it,
   with the list of what to fix, for a limited number of rounds.

2. **Everything together.** What reaches the Digital Twin for the decision is
   assessed together with the Requirements Repository: the five document
   competencies (consistency, non-redundancy, completeness, conciseness,
   traceability). The report goes with it, to guide the Twin's vote and to be
   shown to the PO, who still decides.

Validation itself is deterministic and calls no LLM. Sending an artefact back
uses the normal channel of the agent, so what answers is whatever provider the
system runs with.

    Twin ──draft/proposal──▶ Critic ──validation_report──▶ Twin
                               │
                               └─ a competency failed: validation_return ──▶ the agent fixes ──▶ Critic

The Critic never breaks the flow it watches: if validation itself fails, the
error goes to the log and the artefact goes on as it was.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..messages import CRITIC, LEADER, REPOSITORY, SYSTEM, TWIN
from ..validation import (
    Artifact,
    CheckConfig,
    Envelope,
    EvidenceRef,
    Finding,
    Policy,
    Severity,
    ValidationEngine,
    ValidationReport,
    host,
    to_agent_feedback,
    to_summary,
)
from ..validation.judge import LLMJudge
from ..validation.policy import DEFAULT_POLICY
from .base import Agent

STAGES = {
    "proposal": "what the Twin proposes to the PO (item level)",
    "draft": "what the Twin drafted from one utterance of a meeting (item level)",
    "recommendation": "the draft as the Consultative Council recommends it, and what it claims about it (item level)",
    "consolidated": "what reaches the Twin for the decision, together with the repository (item and document level)",
    "repository": "the whole Requirements Repository (item and document level)",
}

#: Checks about what an expert claims of a draft (a link, a repetition), not about the draft itself.
CLAIM_CHECKS = ("grounding.links_resolvable", "grounding.duplicate_claim")

FOR_THE_TWIN = (
    "\n\nIf a point needs information only the PO has (a number, a limit, who the user is), "
    "ask the PO instead of inventing it."
    # The first run with a real LLM told the PO "I am resending with corrections" about a
    # version the PO never saw.
    "\nThe PO has not seen the returned version. Write \"message_to_po\" as your proposal to the "
    "PO, without mentioning this return or the corrections."
)


@dataclass
class ValidationSettings:
    """How the Critic behaves.

    rounds  how many times an artefact may go back to the agent that wrote it
            before it goes on with its report. 0 = score and report, never return.
    strict  in a meeting processed in batch (no PO), what still fails after the
            rounds is discarded instead of going to the repository.
    full_reports  keep the whole report in each message of the Critic, not only
            its compact form. An audit trail about five times larger.
    judge   tier 2: an LLM judge for what the deterministic checks cannot see
            (validation/judge.py). Off by default; each call is in the log.
    """

    rounds: int = 1
    strict: bool = False
    full_reports: bool = False
    judge: bool = False
    policy: Policy = DEFAULT_POLICY
    config: CheckConfig = field(default_factory=CheckConfig)


def _fingerprint(artifacts: list[Artifact]) -> str:
    """Content of the artefacts, to tell whether an agent changed anything."""
    return Envelope(artifacts=artifacts).content_hash()


def _one_line(stage: str, ids: list[str], report: ValidationReport) -> str:
    counts = {s: len(report.findings_by_severity(s)) for s in (Severity.BLOCKER, Severity.MAJOR, Severity.MINOR)}
    text = f"{stage} {', '.join(ids[:4])}{'…' if len(ids) > 4 else ''}: {report.verdict.label}"
    failed = report.failed_dimensions()
    if failed:
        text += " · did not pass: " + ", ".join(d.value for d in failed)
    found = [f"{n} {s.label}" for s, n in counts.items() if n]
    return text + (" · " + ", ".join(found) if found else "")


class Critic(Agent):
    name = CRITIC

    def __init__(self, ctx, settings: ValidationSettings | None = None):
        super().__init__(ctx)
        self.settings = settings or ValidationSettings()
        #: Baselines of the evaluation are scored but never sent back (see meeting.py).
        self.observe_only = False
        #: The judge goes through call_llm, so its calls are in the log like any agent's.
        self.judge = LLMJudge(self.call_llm, name=getattr(ctx.provider, "name", "llm")) if self.settings.judge else None
        self.engine = ValidationEngine(config=self.settings.config, policy=self.settings.policy,
                                       trace=host.RepositoryTraceability(ctx.db), judge=self.judge)

    # ------------------------------------------------------------------ assessment

    def _rounds(self) -> int:
        return 0 if self.observe_only else max(0, self.settings.rounds)

    def _failed(self, stage: str, exc: Exception) -> None:
        """Validation failed, not the artefact: say so in the log and let the flow go on."""
        self.send(SYSTEM, "error", {"role": "critic", "stage": stage, "error": f"{type(exc).__name__}: {exc}"})

    def _context(self, candidates: list[Artifact]) -> list[Artifact]:
        """The active repository, without what the candidates are a new version of."""
        replaced = {link.target for c in candidates for link in c.links if link.kind == "base"}
        return host.repository_envelope(self.ctx.db, with_sources=False, exclude=replaced).artifacts

    def assess(self, candidates: list[Artifact], sources: list, *, stage: str, recipient: str,
               round_: int = 0, together: bool = False) -> ValidationReport:
        """Score the candidates and tell the agent that produced them.

        `together` False: the five item competencies of each candidate.
        `together` True: the same, plus the five document competencies of the
        candidates taken with the repository.
        """
        ids = [c.id for c in candidates]
        needs_context = together or any(c.links for c in candidates)
        envelope = Envelope(artifacts=candidates + (self._context(candidates) if needs_context else []), sources=list(sources))
        report = self.engine.validate(envelope, focus=set(ids), document_level=together)
        self.send(recipient, "validation_report", host.report_content(
            report, stage=stage, summary=_one_line(stage, ids, report), full=self.settings.full_reports,
            round=round_, artefacts=ids))
        return report

    def _return(self, recipient: str, stage: str, report: ValidationReport, round_: int, text: str,
                returned: list[dict] | None = None) -> None:
        """`returned`: what goes back, when no other message of the log keeps it.

        A draft and a recommendation are already in the messages of the agents that wrote
        them; a proposal is not, and without it the correction cannot be compared.
        """
        content = {
            "stage": stage, "round": round_, "artefacts": [i.artifact_id for i in report.item_results],
            "failed_dimensions": [d.value for d in report.failed_dimensions()], "text": text,
        }
        if returned is not None:
            content["returned"] = returned
        self.send(recipient, "validation_return", content)

    @staticmethod
    def _item_summary(report: ValidationReport, artifact_id: str, *, findings: bool = True, **extra) -> dict:
        """What is kept next to one requirement: its scores and the report they are in.

        The findings come along when someone is going to read them (the PO, the
        Twin before its vote); otherwise they stay in the Critic's message, which
        `report_id` points to.
        """
        summary = to_summary(report)
        item = summary["items"].get(artifact_id, {})
        data = {
            "report_id": report.run_id,
            "verdict": item.get("verdict", summary["verdict"]),
            "passed": item.get("passed", summary["passed"]),
            "score": item.get("score"),
            "scores": {d: s["score"] for d, s in item.get("scores", {}).items()},
            "failed_dimensions": [d for d, s in item.get("scores", {}).items() if not s["passed"]],
            **extra,
        }
        if findings:
            data["findings"] = [f for f in summary["findings"] if artifact_id in f["target"].split("|")]
        return data

    # ------------------------------------------------------------------ conversation with the PO

    def _proposal_artifacts(self, requirements: list[dict]) -> tuple[list[Artifact], list]:
        evidence, sources = host.conversation_evidence(self.ctx.db, self.ctx.conversation_id)
        ids = host.proposal_ids(self.ctx.db, requirements)
        return [host.artifact_from_requirement(host.with_run_links(r, ids), f"P{i}", evidence=evidence)
                for i, r in enumerate(requirements, start=1)], sources

    def gate_proposal(self, twin, decision: dict, rework) -> dict:
        """Gate of the Twin's proposal, before the PO sees it.

        `rework(data)` gives the Twin a message and returns its new decision
        without announcing it. If the Twin answers a returned proposal by asking
        the PO or consulting the experts, that is its decision and it stands.
        """
        latest = [decision]  # what the Twin last decided, whatever happens to validation after that
        try:
            return self._gate_proposal(twin, latest, rework)
        except Exception as exc:
            self._failed("proposal", exc)
            return latest[0]

    def _gate_proposal(self, twin, latest: list[dict], rework) -> dict:
        rounds, unchanged = 0, False
        while True:
            decision = latest[0]
            candidates, sources = self._proposal_artifacts(decision["requirements"])
            report = self.assess(candidates, sources, stage="proposal", recipient=twin.name, round_=rounds)
            if report.passed or rounds >= self._rounds():
                break
            rounds += 1
            feedback = to_agent_feedback(report) + FOR_THE_TWIN
            self._return(twin.name, "proposal", report, rounds, feedback, returned=decision["requirements"])
            revised = rework({"from": "system", "notice": feedback})
            latest[0] = revised
            if revised["action"] != "propose":
                return revised
            if _fingerprint(self._proposal_artifacts(revised["requirements"])[0]) == _fingerprint(candidates):
                unchanged = True
                break

        decision = latest[0]
        together = self.assess(candidates, sources, stage="consolidated", recipient=twin.name, together=True)
        for artifact, requirement in zip(candidates, decision["requirements"]):
            requirement["validation"] = self._item_summary(together, artifact.id, rounds=rounds)
        decision["validation"] = {**to_summary(together), "rounds": rounds, "unchanged": unchanged,
                                  "feedback": None if together.passed else to_agent_feedback(together)}
        return decision

    # ------------------------------------------------------------------ meeting in batch

    @staticmethod
    def _turn_sources(project: dict) -> list:
        return host.sources_from_turns(project["turns"], f"meeting {project['id']}")

    @staticmethod
    def _draft_artifact(draft: dict, recommendation: dict | None = None, final: dict | None = None) -> Artifact:
        """The draft as an artefact: as drafted, as recommended or as decided."""
        content = dict(draft)
        links: list[dict] = []
        for change in (recommendation, final):
            if not change:
                continue
            for key in ("statement", "class", "subtype"):
                if change.get(key):
                    content[key] = change[key]
            if change.get("class") == "FR":
                content["subtype"] = None
            links = list(change.get("links") or [])
        if recommendation and recommendation.get("duplicate_of") and not final:
            links.append({"kind": "duplicates", "target": recommendation["duplicate_of"]})
        evidence = [EvidenceRef(source_id=t) for t in draft.get("turns") or []]
        # The motive of a recommendation or of a vote is about the decision, not part of
        # the requirement: leaving it out lets an unchanged requirement be recognised as such.
        return host.artifact_from_requirement(content, f"D{draft.get('index', 0) + 1}", evidence=evidence,
                                              links=links, base=None, rationale=draft.get("reason") or "")

    def gate_draft(self, twin, draft: dict, project: dict) -> dict:
        """Gate of a draft the Twin wrote from one utterance. Returns the draft that goes on."""
        latest = [draft]
        try:
            return self._gate_draft(twin, latest, project)
        except Exception as exc:
            self._failed("draft", exc)
            return latest[0]

    def _gate_draft(self, twin, latest: list[dict], project: dict) -> dict:
        sources = self._turn_sources(project)
        rounds, unchanged = 0, False
        while True:
            draft = latest[0]
            candidate = self._draft_artifact(draft)
            report = self.assess([candidate], sources, stage="draft", recipient=twin.name, round_=rounds)
            if report.passed or rounds >= self._rounds():
                break
            rounds += 1
            feedback = to_agent_feedback(report)
            self._return(twin.name, "draft", report, rounds, feedback)
            utterances = {t["id"]: t["text"] for t in project["turns"] if t["id"] in (draft.get("turns") or [])}
            revised = twin.revise(draft, feedback, utterances, project.get("language", "en"))
            if not revised or _fingerprint([self._draft_artifact(revised)]) == _fingerprint([candidate]):
                unchanged = True
                break
            latest[0] = revised
        return {**latest[0], "validation": self._item_summary(report, candidate.id, findings=False, rounds=rounds, unchanged=unchanged)}

    @staticmethod
    def _changes_the_artefact(recommendation: dict) -> bool:
        return recommendation["action"] == "fix" or bool(recommendation.get("links")) or bool(recommendation.get("duplicate_of"))

    @staticmethod
    def _blame(report: ValidationReport, already_failed: set[str]) -> list[Finding]:
        """Findings that are the Consultative Council's doing, not the draft's.

        A false claim (a link to a requirement that does not exist, a repetition
        that is not one), or a competency the draft passed and the recommended
        version does not.
        """
        regressed = {d for d in report.failed_dimensions() if d.value not in already_failed}
        return [
            f for f in report.all_findings()
            if (f.check_id in CLAIM_CHECKS and f.severity in (Severity.BLOCKER, Severity.MAJOR))
            or (f.dimension in regressed and f.severity is not Severity.INFO)
        ]

    def gate_recommendation(self, leader, draft: dict, recommendation: dict, utterances: dict, project: dict) -> dict:
        """Gate of what the Consultative Council recommends for a draft.

        A recommendation that leaves the draft as it is has nothing new to
        validate. One that rewrites it, links it or calls it a repetition is
        assessed, and goes back to the Leader (and through it to the expert) if
        what it adds does not hold.
        """
        latest = [recommendation]
        try:
            return self._gate_recommendation(leader, draft, latest, utterances, project)
        except Exception as exc:
            self._failed("recommendation", exc)
            return latest[0]

    def _gate_recommendation(self, leader, draft: dict, latest: list[dict], utterances: dict, project: dict) -> dict:
        if not self._changes_the_artefact(latest[0]):
            return latest[0]
        sources = self._turn_sources(project)
        already_failed = set((draft.get("validation") or {}).get("failed_dimensions") or [])
        rounds, unchanged = 0, False
        while True:
            recommendation = latest[0]
            candidate = self._draft_artifact(draft, recommendation)
            report = self.assess([candidate], sources, stage="recommendation", recipient=LEADER, round_=rounds)
            blame = self._blame(report, already_failed)
            if not blame or rounds >= self._rounds():
                break
            rounds += 1
            feedback = to_agent_feedback(report, only=blame)
            self._return(LEADER, "recommendation", report, rounds, feedback)
            revised = leader.consult_draft(draft, utterances, project.get("language", "en"), feedback=feedback)
            if revised["action"] == recommendation["action"] and \
                    _fingerprint([self._draft_artifact(draft, revised)]) == _fingerprint([candidate]):
                unchanged = True
                break
            latest[0] = revised
            if not self._changes_the_artefact(revised):
                # The council took back what it had added: the draft stands as the Twin wrote it.
                return {**revised, "validation": {"passed": True, "rounds": rounds, "unchanged": False, "findings": []}}
        blamed = {(b.check_id, b.message) for b in blame}
        summary = self._item_summary(report, candidate.id, rounds=rounds, unchanged=unchanged)
        summary.update(passed=not blame, findings=[f for f in summary["findings"] if (f["check_id"], f["message"]) in blamed])
        return {**latest[0], "validation": summary}

    def guide(self, draft: dict, recommendation: dict, project: dict) -> dict | None:
        """What the Twin receives with the recommendation, before its vote.

        A draft the council recommends discarding is leaving, so there is nothing
        to assess, unless the Critic could not confirm the reason given for
        discarding it: then the Twin must know before it votes.
        """
        confirmed = (recommendation.get("validation") or {}).get("passed", True)
        if recommendation["action"] == "discard" and confirmed:
            return None
        return self.consolidate(draft, recommendation, project)

    def consolidate(self, draft: dict, recommendation: dict | None, project: dict, final: dict | None = None) -> dict | None:
        """Everything together: the candidate with the repository, and the report that guides the decision.

        Without `final`, it is the candidate as it reaches the Twin for the vote.
        With `final`, it is the requirement exactly as the Twin decided to save it.
        Returns None if validation itself failed.
        """
        try:
            candidate = self._draft_artifact(draft, recommendation, final)
            report = self.assess([candidate], self._turn_sources(project), stage="consolidated", recipient=TWIN, together=True)
            rounds = (draft.get("validation") or {}).get("rounds", 0) + ((recommendation or {}).get("validation") or {}).get("rounds", 0)
            return {**self._item_summary(report, candidate.id, rounds=rounds),
                    "passed": report.passed, "verdict": report.verdict.value,
                    "failed_dimensions": [d.value for d in report.failed_dimensions()],
                    "document": to_summary(report)["document"], "fingerprint": _fingerprint([candidate])}
        except Exception as exc:
            self._failed("consolidated", exc)
            return None

    def for_saving(self, draft: dict, recommendation: dict | None, final: dict, project: dict, guidance: dict | None) -> dict | None:
        """The assessment of the requirement as it will be saved (the one decided, if the Twin changed it)."""
        try:
            if guidance and guidance.get("fingerprint") == _fingerprint([self._draft_artifact(draft, recommendation, final)]):
                return guidance
        except Exception as exc:
            self._failed("consolidated", exc)
            return None
        return self.consolidate(draft, recommendation, project, final)

    def closes_the_gate(self, check: dict | None) -> bool:
        """Strict mode, in a meeting without a PO: what still fails is not saved, and the log says so."""
        if not check or not self.settings.strict or self.observe_only or check["passed"]:
            return False
        self.send(TWIN, "validation_return", {
            "stage": "consolidated", "round": check.get("rounds", 0), "artefacts": [check["report_id"]],
            "failed_dimensions": check["failed_dimensions"],
            "text": "Not saved: in strict mode, a requirement that still fails validation is discarded "
                    f"(did not pass: {', '.join(check['failed_dimensions'])}).",
        })
        return True

    # ------------------------------------------------------------------ the repository

    def review_repository(self, recipient: str = REPOSITORY) -> dict | None:
        """The whole repository: the five item competencies of each requirement and the five of the set."""
        try:
            envelope = host.repository_envelope(self.ctx.db)
            report = self.engine.validate(envelope)
            ids = [a.id for a in envelope.artifacts]
            content = host.report_content(report, stage="repository", full=self.settings.full_reports, max_findings=1000,
                                          suggestions=False, artefacts=len(ids),
                                          summary=_one_line("repository", [f"{len(ids)} requirement(s)"], report))
            self.send(recipient, "validation_report", content)
        except Exception as exc:
            self._failed("repository", exc)
            return None
        # What the caller keeps: the verdict and the scores of the set. The findings are in the message.
        items = content["items"].values()
        return {
            **{k: content[k] for k in ("report_id", "verdict", "passed", "failed_dimensions", "document", "counts", "envelope_hash")},
            "requirements": len(ids),
            "requirements_passed": sum(1 for item in items if item["passed"]),
        }

    def validate_repository(self, focus: set[str] | None = None) -> ValidationReport:
        """Same as review_repository, without telling anyone: for the command line and the API.

        With `focus`, those requirements are assessed against the rest of the
        repository. A requirement that is no longer active (superseded, split,
        merged) can still be looked at: the inactive ones are then read too.
        """
        envelope = host.repository_envelope(self.ctx.db)
        if focus and not all(envelope.artifact(i) for i in focus):
            envelope = host.repository_envelope(self.ctx.db, only_active=False)
        return self.engine.validate(envelope, focus=focus)
