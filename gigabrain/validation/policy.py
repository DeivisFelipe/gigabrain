"""Verdict policy: how a set of findings becomes a decision.

Keeping this apart from the checks is a design decision. A check says
what it found; the policy says what to do about it. Changing the ruler, to be
more or less strict, becomes a change of configuration and not of code, and the
paper can report exactly which ruler was used in each run.

The rule of the gate is stated per competency: every artefact gets
one score per dimension, and **it passes only if every dimension passes**. A
dimension passes when it reaches `min_dimension_score` and has no blocking
finding. What does not pass goes back to the agent that wrote it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Finding, Severity, Verdict

#: Version of the policy. It goes into the report so that two runs are only
#: comparable when they ran under the same ruler. 2.0: the gate became
#: per-competency (the cap on the number of major findings was dropped).
POLICY_VERSION = "2.0"


@dataclass(frozen=True)
class Policy:
    """Ruler for approving and for discounting the score.

    `penalties` is how much each finding takes from the score of its dimension
    (before the weight of the check). The other fields say when the gate closes.
    """

    version: str = POLICY_VERSION

    penalties: dict[Severity, float] = field(
        default_factory=lambda: {
            Severity.BLOCKER: 0.50,
            Severity.MAJOR: 0.25,
            Severity.MINOR: 0.10,
            Severity.INFO: 0.02,
        }
    )

    #: Minimum score of each dimension. This is the competency threshold: below
    #: it, the dimension fails and so does the artefact. 0.0 switches it off.
    min_dimension_score: float = 0.70
    #: A blocking finding fails its dimension whatever the score: "blocking"
    #: means the artefact is unusable (no acceptance criteria, a cited source
    #: that does not exist), not merely poor.
    max_blockers: int = 0
    #: Optional caps on the number of findings, over all dimensions. `None` = no
    #: cap. The default ruler has none: the decision is per competency.
    max_majors: int | None = None
    max_minors: int | None = None

    def penalty_for(self, severity: Severity) -> float:
        return self.penalties.get(severity, 0.0)

    # -- gate --------------------------------------------------------------------

    def dimension_passes(self, score: float, findings: list[Finding]) -> bool:
        """Whether one dimension passes: minimum score and no blocking finding."""
        blockers = sum(1 for f in findings if f.severity is Severity.BLOCKER)
        return blockers <= self.max_blockers and score >= self.min_dimension_score

    def decide(self, findings: list[Finding], dimension_scores: dict) -> Verdict:
        """Apply the ruler to a set of findings and scores.

        `dimension_scores` maps Dimension -> DimensionScore (or a float); both are
        accepted so that the policy can be used at the item level and at the
        document level without an adapter.
        """
        blockers = sum(1 for f in findings if f.severity is Severity.BLOCKER)
        majors = sum(1 for f in findings if f.severity is Severity.MAJOR)
        minors = sum(1 for f in findings if f.severity is Severity.MINOR)

        if blockers > self.max_blockers:
            return Verdict.REJECTED
        if self.max_majors is not None and majors > self.max_majors:
            return Verdict.REJECTED
        if self.max_minors is not None and minors > self.max_minors:
            return Verdict.REJECTED

        for value in dimension_scores.values():
            if isinstance(value, (int, float)):
                if float(value) < self.min_dimension_score:
                    return Verdict.REJECTED
            elif not value.passed:
                return Verdict.REJECTED

        significant = [
            f for f in findings if f.severity in (Severity.BLOCKER, Severity.MAJOR, Severity.MINOR)
        ]
        return Verdict.APPROVED_WITH_REMARKS if significant else Verdict.APPROVED

    @staticmethod
    def combine(verdicts: list[Verdict]) -> Verdict:
        """Verdict of the whole: the worst of the partial verdicts."""
        if not verdicts:
            return Verdict.APPROVED
        if any(v is Verdict.REJECTED for v in verdicts):
            return Verdict.REJECTED
        if any(v is Verdict.APPROVED_WITH_REMARKS for v in verdicts):
            return Verdict.APPROVED_WITH_REMARKS
        return Verdict.APPROVED


#: Strict ruler, for the gate right before the PO approves: nothing beyond
#: informative findings passes.
STRICT_POLICY = Policy(
    version=f"{POLICY_VERSION}-strict",
    max_majors=0,
    max_minors=0,
    min_dimension_score=0.85,
)

#: Lenient ruler, to measure the raw output of the agents without failing
#: everything. Only a blocking finding still fails.
LENIENT_POLICY = Policy(
    version=f"{POLICY_VERSION}-lenient",
    min_dimension_score=0.0,
)

DEFAULT_POLICY = Policy()

POLICIES: dict[str, Policy] = {
    "default": DEFAULT_POLICY,
    "strict": STRICT_POLICY,
    "lenient": LENIENT_POLICY,
}
