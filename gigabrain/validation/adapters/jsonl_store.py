"""Persistence adapter: reports in a JSONL file.

A minimal implementation of the ReportStore port for runs outside GigaBrain (the
command line over envelope files). One report per line, in JSON, in the order
they were written: append-only (history is never rewritten), readable by eye and
trivial to import into a database.

Inside GigaBrain the reports go to the event log instead (EventReportStore in
validation/host.py). Both read a report back with `report_from_dict`.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..model import (
    Dimension,
    DimensionScore,
    EvidenceKind,
    Finding,
    ItemResult,
    Level,
    Severity,
    ValidationReport,
    Verdict,
)


class JsonlReportStore:
    """Writes and reads validation reports in a .jsonl file."""

    def __init__(self, path: str | Path = "validation_reports.jsonl") -> None:
        self.path = Path(path)

    # -- writing -------------------------------------------------------------

    def save(self, report: ValidationReport) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(report.to_dict(), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return report.run_id

    # -- reading -------------------------------------------------------------

    def _iter_raw(self):
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def load(self, report_id: str) -> ValidationReport | None:
        for data in self._iter_raw():
            if data.get("run_id") == report_id:
                return report_from_dict(data)
        return None

    def history(self, envelope_hash: str) -> list[ValidationReport]:
        return [
            report_from_dict(data)
            for data in self._iter_raw()
            if data.get("envelope_hash") == envelope_hash
        ]

    def all(self) -> list[ValidationReport]:
        return [report_from_dict(data) for data in self._iter_raw()]


# --------------------------------------------------------------------------
# Deserialisation
# --------------------------------------------------------------------------


def _finding_from_dict(data: dict) -> Finding:
    return Finding(
        check_id=data["check_id"],
        dimension=Dimension(data["dimension"]),
        severity=Severity(data["severity"]),
        message=data.get("message", ""),
        level=Level(data.get("level", "item")),
        target_id=data.get("target_id", ""),
        locator=data.get("locator", ""),
        excerpt=data.get("excerpt", ""),
        suggestion=data.get("suggestion", ""),
        evidence_kind=EvidenceKind(data.get("evidence_kind", "deterministic")),
    )


def _scores_from_dict(data: dict) -> dict[Dimension, DimensionScore]:
    out: dict[Dimension, DimensionScore] = {}
    for key, value in (data or {}).items():
        dimension = Dimension(key)
        out[dimension] = DimensionScore(
            dimension=dimension,
            score=float(value.get("score", 1.0)),
            likert=float(value.get("likert", 5.0)),
            findings=int(value.get("findings", 0)),
            penalty=float(value.get("penalty", 0.0)),
            passed=bool(value.get("passed", True)),
            checks=int(value.get("checks", 0)),
        )
    return out


def report_from_dict(data: dict) -> ValidationReport:
    report = ValidationReport(
        run_id=data.get("run_id", ""),
        envelope_hash=data.get("envelope_hash", ""),
        ruleset_version=data.get("ruleset_version", ""),
        policy_version=data.get("policy_version", ""),
        created_at=data.get("created_at", ""),
        verdict=Verdict(data.get("verdict", "approved")),
        checks_run=list(data.get("checks_run", [])),
        checks_skipped=dict(data.get("checks_skipped", {})),
        notes=list(data.get("notes", [])),
        focus=list(data.get("focus", [])),
        document_assessed=bool(data.get("document_assessed", True)),
    )
    report.document_findings = [_finding_from_dict(f) for f in data.get("document_findings", [])]
    report.document_scores = _scores_from_dict(data.get("document_scores", {}))
    report.item_results = [
        ItemResult(
            artifact_id=item.get("artifact_id", ""),
            scores=_scores_from_dict(item.get("scores", {})),
            findings=[_finding_from_dict(f) for f in item.get("findings", [])],
            verdict=Verdict(item.get("verdict", "approved")),
        )
        for item in data.get("item_results", [])
    ]
    return report
