"""Validation engine: the deterministic core of the Critic.

Responsibility: given an envelope, run the registered checks, gather the
findings, compute the scores, apply the policy and return a versioned report.

The engine does not know *what* quality is (that is in the checks) nor *when* to
fail (that is in the policy). It knows how to orchestrate, isolate failures and
record what ran. That separation is what makes the experiment of the paper
feasible: changing the ruler, changing the rule set or switching checks off does
not touch this file.

Two tiers:
  1. deterministic, always on, no network and no external dependency;
  2. LLM judge, optional, called only for what the first one does not decide.
Tier 2 is off by default (NullJudgeProvider). When a judge is plugged in, it
answers only the questions of `judge.QUESTIONS`, after the checks of each
artefact. Its findings are marked EvidenceKind.LLM_JUDGMENT and count in the
gate; a judge that fails adds a note, never a finding.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from . import checks as _checks  # noqa: F401  (registers the checks in the REGISTRY)
from . import judge as _judge
from .model import (
    Artifact,
    Envelope,
    Finding,
    ItemResult,
    Level,
    ValidationReport,
)
from .policy import DEFAULT_POLICY, Policy
from .ports import (
    ClockProvider,
    JudgeProvider,
    NullJudgeProvider,
    NullTraceabilityProvider,
    SystemClock,
    TraceabilityProvider,
)
from .registry import REGISTRY, CheckConfig, CheckContext, CheckRegistry
from .scoring import RULESET_VERSION, score_document, score_item

SWITCHED_OFF = "switched off in the configuration"


class ValidationEngine:
    """Runs the validation of an envelope and produces a ValidationReport."""

    def __init__(
        self,
        *,
        config: CheckConfig | None = None,
        policy: Policy | None = None,
        registry: CheckRegistry | None = None,
        trace: TraceabilityProvider | None = None,
        judge: JudgeProvider | None = None,
        clock: ClockProvider | None = None,
    ) -> None:
        self.config = config or CheckConfig()
        self.policy = policy or DEFAULT_POLICY
        self.registry = registry or REGISTRY
        self.trace = trace or NullTraceabilityProvider()
        self.judge = judge or NullJudgeProvider()
        self.clock = clock or SystemClock()

    # -- main API ------------------------------------------------------------

    def validate(
        self,
        envelope: Envelope,
        *,
        focus: set[str] | frozenset[str] | list[str] | None = None,
        document_level: bool = True,
    ) -> ValidationReport:
        """Validate the envelope.

        `focus` restricts the run to some artefacts, the rest of the envelope
        being context (a candidate against the saved repository). `document_level`
        False gives the five item scores only, which is what the gate of a single
        agent needs; True adds the five document scores, "everything together".
        """
        focus_ids = frozenset(focus or ())
        ctx = CheckContext(envelope=envelope, config=self.config, trace=self.trace, focus=focus_ids)

        report = ValidationReport(
            run_id=envelope.run_id or f"run-{uuid.uuid4().hex[:12]}",
            envelope_hash=envelope.content_hash(),
            ruleset_version=RULESET_VERSION,
            policy_version=self.policy.version,
            created_at=self.clock.now_iso(),
            focus=sorted(focus_ids),
            document_assessed=document_level,
        )

        if self.judge.available:
            report.judge = getattr(self.judge, "name", "llm")
        item_findings = self._run_item_checks(ctx, report)
        for artifact in ctx.focus_artifacts():
            findings = item_findings.get(artifact.id, [])
            scores = score_item(findings, policy=self.policy, registry=self.registry, checks_run=report.checks_run)
            report.item_results.append(
                ItemResult(
                    artifact_id=artifact.id,
                    scores=scores,
                    findings=findings,
                    verdict=self.policy.decide(findings, scores),
                )
            )

        verdicts = [item.verdict for item in report.item_results]
        if document_level:
            report.document_findings = self._run_document_checks(ctx, report)
            report.document_scores = score_document(
                report.document_findings, policy=self.policy, registry=self.registry, checks_run=report.checks_run
            )
            verdicts.append(self.policy.decide(report.document_findings, report.document_scores))
        report.verdict = Policy.combine(verdicts)

        self._add_notes(report, envelope, focus_ids)
        return report

    def validate_artifact(self, artifact: Artifact, envelope: Envelope) -> ItemResult:
        """Validate a single artefact in the context of the envelope (item level only)."""
        ctx = CheckContext(envelope=envelope, config=self.config, trace=self.trace, focus=frozenset({artifact.id}))
        ran = [c.id for c in self.registry.by_level(Level.ITEM) if self._skip_reason(c, ctx) is None]
        findings = self._check_one_artifact(artifact, ctx, notes=[])
        scores = score_item(findings, policy=self.policy, registry=self.registry, checks_run=ran)
        return ItemResult(
            artifact_id=artifact.id,
            scores=scores,
            findings=findings,
            verdict=self.policy.decide(findings, scores),
        )

    # -- running the checks ----------------------------------------------------

    def _enabled(self, check_id: str) -> bool:
        return check_id not in self.config.disabled_checks

    def _skip_reason(self, check, ctx: CheckContext) -> str | None:
        """Why this check must not run, or None if it must.

        Two possible reasons: it was switched off in the configuration
        (ablation), or the run does not carry the material it depends on. The
        second one is what keeps the report honest: the check says it could not
        look, instead of failing the artefact for missing material.
        """
        if not self._enabled(check.id):
            return SWITCHED_OFF
        if check.applies is not None:
            return check.applies(ctx)
        return None

    def _apply_overrides(self, finding: Finding) -> Finding:
        override = self.config.severity_overrides.get(finding.check_id)
        return replace(finding, severity=override) if override else finding

    def _check_one_artifact(self, artifact: Artifact, ctx: CheckContext, notes: list[str]) -> list[Finding]:
        findings: list[Finding] = []
        for check in self.registry.by_level(Level.ITEM):
            if self._skip_reason(check, ctx) is not None:
                continue
            findings.extend(self._safe_run(check, lambda c=check: c.fn(artifact, ctx)))
        findings.extend(self._ask_the_judge(artifact, ctx, notes))
        return [self._apply_overrides(f) for f in findings]

    def _ask_the_judge(self, artifact: Artifact, ctx: CheckContext, notes: list[str]) -> list[Finding]:
        """Tier 2, after the checks. Without material the question is not asked ("I do not know")."""
        if not self.judge.available:
            return []
        findings: list[Finding] = []
        for question in _judge.QUESTIONS:
            if not self._enabled(question.id):
                continue
            payload = _judge.material(artifact, ctx)
            if payload is None:
                continue
            try:
                findings.extend(self.judge.judge(question=question.id, dimension=question.dimension,
                                                 target_id=artifact.id, payload=payload))
            except Exception as exc:
                notes.append(f"The LLM judge failed on {artifact.id} ({question.id}): {exc}. Nothing was concluded from it.")
        return findings

    def _run_item_checks(self, ctx: CheckContext, report: ValidationReport) -> dict[str, list[Finding]]:
        for check in self.registry.by_level(Level.ITEM):
            reason = self._skip_reason(check, ctx)
            if reason is None:
                report.checks_run.append(check.id)
            else:
                report.checks_skipped[check.id] = reason
        if self.judge.available:
            for question in _judge.QUESTIONS:
                if self._enabled(question.id):
                    report.checks_run.append(question.id)
                else:
                    report.checks_skipped[question.id] = SWITCHED_OFF

        return {artifact.id: self._check_one_artifact(artifact, ctx, report.notes) for artifact in ctx.focus_artifacts()}

    def _run_document_checks(self, ctx: CheckContext, report: ValidationReport) -> list[Finding]:
        findings: list[Finding] = []
        for check in self.registry.by_level(Level.DOCUMENT):
            reason = self._skip_reason(check, ctx)
            if reason is not None:
                report.checks_skipped[check.id] = reason
                continue
            report.checks_run.append(check.id)
            findings.extend(self._safe_run(check, lambda c=check: c.fn(ctx)))
        return [self._apply_overrides(f) for f in findings]

    def _safe_run(self, check, call) -> list[Finding]:
        """Run a check isolating its failures.

        A check that breaks cannot bring the whole validation down: the report
        has to come out, saying which rule failed. That matters all the more
        because other members of the group will write new checks.
        """
        try:
            return list(call())
        except Exception as exc:  # pragma: no cover - defensive path
            return [
                Finding(
                    check_id=check.id,
                    dimension=check.dimension,
                    severity=check.default_severity,
                    level=check.level,
                    message=f"The check '{check.id}' failed to run: {exc!r}",
                    suggestion="This is an error in the check, not in the artefact.",
                )
            ]

    # -- notes about the run -----------------------------------------------------

    def _add_notes(self, report: ValidationReport, envelope: Envelope, focus: frozenset[str]) -> None:
        if not self.judge.available:
            report.notes.append(
                "Tier 2 (LLM judge) is off: every finding is deterministic and can be "
                "reproduced from the input hash."
            )
        else:
            report.notes.append(
                f"Tier 2 (LLM judge, {report.judge}) is on: findings marked llm_judgment come from a "
                "language model and cannot be reproduced from the input hash."
            )
        if isinstance(self.trace, NullTraceabilityProvider):
            report.notes.append(
                "No traceability provider: links that point outside this run could not be checked."
            )
        if not envelope.sources:
            report.notes.append(
                "Run without sources: the grounding and coverage checks had nothing to compare with."
            )
        if focus:
            context = len(envelope.artifacts) - len(report.item_results)
            report.notes.append(
                f"Focused run: {len(report.item_results)} artefact(s) assessed against {context} artefact(s) of context."
            )
        if not report.document_assessed:
            report.notes.append("Item level only: the document dimensions were not assessed in this run.")
        elif envelope.document is None:
            report.notes.append(
                "Run without an assembled document: the section completeness checks were skipped."
            )


def validate(envelope: Envelope, **kwargs) -> ValidationReport:
    """Shortcut: build an engine with the defaults and validate."""
    return ValidationEngine(**kwargs).validate(envelope)
