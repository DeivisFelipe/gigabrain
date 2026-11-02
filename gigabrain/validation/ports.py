"""Ports of the validation layer.

Each port is a dependency validation *uses* but does not *own*. They exist so
that validation can be written and tested on its own, and so that plugging it
into the rest of the project is a one-line change.

Ports and the adapters that implement them today (validation/host.py):

    ReportStore            -> the event log (log.db), through EventReportStore
    TraceabilityProvider   -> the Requirements Repository, through RepositoryTraceability
    JudgeProvider          -> second tier, LLM judge (validation/judge.py, off by default)
    ClockProvider          -> injected to make a report reproducible

Scope decision: validation **verifies** traceability, it does not
**manage** it. The repository keeps the links and the versions; validation asks
it questions and says whether the answers support what the agents claim.
"""

from __future__ import annotations

import datetime
from typing import Protocol, runtime_checkable

from .model import Dimension, Finding, ValidationReport


@runtime_checkable
class ReportStore(Protocol):
    """Persistence of the validation reports."""

    def save(self, report: ValidationReport) -> str:
        """Store the report and return an identifier / locator."""
        ...

    def load(self, report_id: str) -> ValidationReport | None: ...

    def history(self, envelope_hash: str) -> list[ValidationReport]:
        """Earlier reports of the same input: the basis for comparing runs."""
        ...


@runtime_checkable
class TraceabilityProvider(Protocol):
    """Queries to the traceability data kept outside validation."""

    def knows(self, requirement_id: str) -> bool:
        """Whether the id exists in the history of the project (not only in this run)."""
        ...

    def predecessors(self, requirement_id: str) -> list[str]:
        """Requirements this one evolved from (R3 supersedes R1 -> [R1])."""
        ...

    def impacted_by(self, requirement_id: str) -> list[str]:
        """Requirements affected by a change in this one (used for impact analysis)."""
        ...


@runtime_checkable
class JudgeProvider(Protocol):
    """Second validation tier: judgement by an LLM.

    It is only called for what the deterministic tier cannot decide. Its result
    enters the report marked as EvidenceKind.LLM_JUDGMENT and can be switched
    off entirely for ablation.
    """

    @property
    def available(self) -> bool: ...

    def judge(
        self,
        *,
        question: str,
        dimension: Dimension,
        target_id: str,
        payload: dict,
    ) -> list[Finding]:
        """Return the findings (possibly none) for the question asked."""
        ...


@runtime_checkable
class ClockProvider(Protocol):
    def now_iso(self) -> str: ...


# --------------------------------------------------------------------------
# Neutral implementations: the default when there is nothing to plug in
# --------------------------------------------------------------------------


class NullTraceabilityProvider:
    """Knows nothing beyond what is in the run itself.

    Important: the checks must treat "I do not know" as *unknown*, never as
    *missing*. Failing a requirement because no traceability data was plugged in
    would be a false negative of the architecture.
    """

    def knows(self, requirement_id: str) -> bool:
        return False

    def predecessors(self, requirement_id: str) -> list[str]:
        return []

    def impacted_by(self, requirement_id: str) -> list[str]:
        return []


class NullJudgeProvider:
    """Judge switched off: validation runs entirely deterministic."""

    @property
    def available(self) -> bool:
        return False

    def judge(self, **_kwargs) -> list[Finding]:
        return []


class SystemClock:
    def now_iso(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class FixedClock:
    """Frozen clock, used in the tests so that reports can be compared."""

    def __init__(self, value: str = "2026-01-01T00:00:00+00:00") -> None:
        self._value = value

    def now_iso(self) -> str:
        return self._value
