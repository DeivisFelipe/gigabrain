"""Presentation of the validation report.

Four renderings, four readers:
- Markdown, for the PO to read;
- feedback, for the agent that wrote the artefact and has to fix it;
- summary, a compact dictionary that travels inside the messages between agents,
  the API answers and the sources of a saved requirement;
- JSON, the whole report, for the log and for the analysis of the experiment.
"""

from __future__ import annotations

import json

from .model import (
    DOCUMENT_DIMENSIONS,
    ITEM_DIMENSIONS,
    Dimension,
    DimensionScore,
    EvidenceKind,
    Finding,
    Severity,
    ValidationReport,
    Verdict,
)

_VERDICT_MARK = {
    Verdict.APPROVED: "APPROVED",
    Verdict.APPROVED_WITH_REMARKS: "APPROVED WITH REMARKS",
    Verdict.REJECTED: "REJECTED",
}

_SEVERITY_ORDER = (Severity.BLOCKER, Severity.MAJOR, Severity.MINOR, Severity.INFO)

PROXY_NOTE = (
    "The Likert scores are a **declared proxy** of the scale used by the human raters of "
    "ReqInOne, to allow a later comparison. The correlation between the automated score "
    "and expert ratings has not been measured yet."
)


def to_json(report: ValidationReport, *, indent: int = 2) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=indent)


def _finding_line(finding: Finding) -> str:
    where = finding.target_id or "—"
    if finding.locator:
        where = f"{where} · {finding.locator}"
    line = f"- **[{finding.severity.label}]** `{finding.check_id}` ({where}): {finding.message}"
    if finding.excerpt:
        line += f"\n  - excerpt: _{finding.excerpt.strip()[:200]}_"
    if finding.suggestion:
        line += f"\n  - suggestion: {finding.suggestion}"
    if finding.evidence_kind.value != "deterministic":
        line += f"\n  - origin of the finding: {finding.evidence_kind.value}"
    return line


def _gate(score: DimensionScore) -> str:
    if not score.assessed:
        return "not assessed"
    return "pass" if score.passed else "FAIL"


def _scores_table(scores: dict[Dimension, DimensionScore], order: tuple[Dimension, ...]) -> list[str]:
    rows = [
        "| Dimension | Score (0–1) | Likert (1–5) | Findings | Gate |",
        "|---|---|---|---|---|",
    ]
    for dimension in order:
        score = scores.get(dimension)
        if score is None:
            continue
        rows.append(
            f"| {dimension.label} | {score.score:.2f} | {score.likert:.2f} | {score.findings} | {_gate(score)} |"
        )
    return rows


def to_markdown(report: ValidationReport, *, include_passing_items: bool = True) -> str:
    lines: list[str] = []
    a = lines.append

    a("# Validation Report — GigaBrain")
    a("")
    a(f"**Verdict:** {_VERDICT_MARK[report.verdict]}")
    failed = report.failed_dimensions()
    if failed:
        a("")
        a("**Competencies that did not pass:** " + ", ".join(d.label for d in failed))
    a("")
    a("| Field | Value |")
    a("|---|---|")
    a(f"| Run | `{report.run_id}` |")
    a(f"| Input hash | `{report.envelope_hash}` |")
    a(f"| Rule-set version | {report.ruleset_version} |")
    a(f"| Policy version | {report.policy_version} |")
    a(f"| Created at | {report.created_at} |")
    a(f"| Checks run | {len(report.checks_run)} |")
    a(f"| Mean item score | {report.item_overall:.2f} |")
    if report.document_assessed:
        a(f"| Document score | {report.document_overall:.2f} |")
    a("")

    counts = {s: len(report.findings_by_severity(s)) for s in _SEVERITY_ORDER}
    a("## Findings")
    a("")
    a("| Severity | Count |")
    a("|---|---|")
    for severity in _SEVERITY_ORDER:
        a(f"| {severity.label} | {counts[severity]} |")
    a("")

    # -- document ------------------------------------------------------------
    if report.document_assessed:
        a("## Document level (everything together)")
        a("")
        a("_The five parameters ReqInOne uses to rate a specification as a whole._")
        a("")
        lines.extend(_scores_table(report.document_scores, DOCUMENT_DIMENSIONS))
        a("")
        if report.document_findings:
            for severity in _SEVERITY_ORDER:
                for finding in [f for f in report.document_findings if f.severity is severity]:
                    a(_finding_line(finding))
        else:
            a("No findings at the document level.")
        a("")

    # -- items ---------------------------------------------------------------
    a("## Item level (intermediate specification)")
    a("")
    a("_The five parameters ReqInOne uses to rate a single requirement._")
    a("")
    if not report.item_results:
        a("No artefact in this run.")
        a("")
    for item in report.item_results:
        if not include_passing_items and item.verdict is Verdict.APPROVED:
            continue
        a(f"### `{item.artifact_id}`: {_VERDICT_MARK[item.verdict]} (score {item.overall:.2f})")
        a("")
        lines.extend(_scores_table(item.scores, ITEM_DIMENSIONS))
        a("")
        if item.findings:
            for severity in _SEVERITY_ORDER:
                for finding in [f for f in item.findings if f.severity is severity]:
                    a(_finding_line(finding))
        else:
            a("No findings.")
        a("")

    # -- provenance footer -----------------------------------------------------
    if report.checks_skipped:
        a("## Checks skipped")
        a("")
        for check_id, reason in sorted(report.checks_skipped.items()):
            a(f"- `{check_id}`: {reason}")
        a("")

    if report.notes:
        a("## Notes about this run")
        a("")
        for note in report.notes:
            a(f"- {note}")
        a("")

    a("---")
    a("")
    a(PROXY_NOTE)

    return "\n".join(lines)


def to_console(report: ValidationReport) -> str:
    """Short summary of a run, for the terminal."""
    counts = {s: len(report.findings_by_severity(s)) for s in _SEVERITY_ORDER}
    head = f"[{_VERDICT_MARK[report.verdict]}] items={report.item_overall:.2f} "
    if report.document_assessed:
        head += f"document={report.document_overall:.2f} "
    head += f"blocking={counts[Severity.BLOCKER]} major={counts[Severity.MAJOR]} minor={counts[Severity.MINOR]}"
    lines = [head]
    failed = report.failed_dimensions()
    if failed:
        lines.append("  did not pass: " + ", ".join(d.value for d in failed))
    for finding in report.all_findings():
        if finding.severity in (Severity.BLOCKER, Severity.MAJOR):
            lines.append("  " + finding.short())
    return "\n".join(lines)


def _actionable(report: ValidationReport) -> list[Finding]:
    """Findings an agent can act on, the most serious first."""
    return [
        f
        for severity in (Severity.BLOCKER, Severity.MAJOR, Severity.MINOR)
        for f in report.all_findings()
        if f.severity is severity
    ]


def feedback_lines(findings: list[Finding], *, max_items: int = 12) -> list[str]:
    """Numbered, actionable lines for a list of findings (already in the order to show)."""
    lines = []
    for index, finding in enumerate(findings[:max_items], start=1):
        where = finding.target_id or "document"
        if finding.locator:
            where = f"{where} ({finding.locator})"
        entry = f"{index}. [{finding.severity.label}] {where}: {finding.message}"
        if finding.suggestion:
            entry += f" {finding.suggestion}"
        lines.append(entry)
    if len(findings) > max_items:
        lines += ["", f"({len(findings) - max_items} more finding(s) in the full report)"]
    return lines


def to_agent_feedback(report: ValidationReport, *, max_items: int = 12, only: list[Finding] | None = None) -> str:
    """Short feedback addressed to the agent that produced the artefact.

    This is the piece that closes the generation -> validation -> correction
    cycle: instead of the report existing only for a person to read, it goes
    back to the agent as an instruction. Only actionable findings go in
    (blocking, major and minor), the most serious first, cut at `max_items` so
    as not to flood the context. `only` narrows it to some findings of the
    report, for when just part of them is the business of that agent.
    """
    actionable = _actionable(report)
    if only is not None:
        actionable = [f for f in actionable if f in only]
    if not actionable:
        return (
            "Validation found nothing to fix. The artefact follows the template, is grounded "
            "in the declared sources and is verifiable."
        )

    lines = [f"Validation returned the artefact: {_VERDICT_MARK[report.verdict]}."]
    failed = [d for d in report.failed_dimensions() if only is None or any(f.dimension is d for f in actionable)]
    if failed:
        lines.append("Competencies that did not pass: " + ", ".join(d.label for d in failed) + ".")
    lines += [
        "Fix the points below and propose again. Do not add new requirements and do not "
        "change the scope: address only what is listed.",
        "",
    ]
    return "\n".join(lines + feedback_lines(actionable, max_items=max_items))


def _compact(finding: Finding, suggestions: bool = True) -> dict:
    data = {
        "check_id": finding.check_id,
        "dimension": finding.dimension.value,
        "severity": finding.severity.value,
        "target": finding.target_id,
        "message": finding.message,
    }
    if finding.locator:
        data["locator"] = finding.locator
    if finding.evidence_kind is not EvidenceKind.DETERMINISTIC:
        data["evidence"] = finding.evidence_kind.value
    if suggestions and finding.suggestion:
        data["suggestion"] = finding.suggestion
    return data


def _score_summary(scores: dict[Dimension, DimensionScore]) -> dict:
    return {d.value: {"score": s.score, "passed": s.passed, "assessed": s.assessed} for d, s in scores.items()}


def to_summary(report: ValidationReport, *, max_findings: int = 20, suggestions: bool = True) -> dict:
    """Compact form of a report: verdict, the score of each competency and the findings.

    It is what goes inside the messages between the agents and next to a
    requirement in the API. A score is in [0, 1]; its Likert form is 1 + 4 x score.
    """
    actionable = _actionable(report)
    data = {
        "report_id": report.run_id,
        "verdict": report.verdict.value,
        "passed": report.passed,
        "failed_dimensions": [d.value for d in report.failed_dimensions()],
        "items": {
            item.artifact_id: {
                "verdict": item.verdict.value,
                "passed": item.passed,
                "score": round(item.overall, 4),
                "scores": _score_summary(item.scores),
            }
            for item in report.item_results
        },
        "document": _score_summary(report.document_scores) if report.document_assessed else None,
        "findings": [_compact(f, suggestions) for f in actionable[:max_findings]],
        "counts": {s.value: len(report.findings_by_severity(s)) for s in _SEVERITY_ORDER},
        "ruleset_version": report.ruleset_version,
        "policy_version": report.policy_version,
    }
    if report.judge:
        data["judge"] = report.judge  # tier 2 was on, and this model answered it
    return data
