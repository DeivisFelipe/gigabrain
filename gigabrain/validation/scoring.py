"""Scores per dimension.

Model: each dimension starts at 1.0 and loses `penalty(severity) x weight of the
check` for every finding, with a floor at 0. The score is then rescaled to the
1..5 range of the Likert questionnaire the human raters of ReqInOne used.

Why rescale: the goal is not one more number, it is to be able to
compare the automated score with expert ratings on the same scale and, later,
to measure the correlation between the two. Until that correlation is measured,
`likert` is a **declared proxy**, not a demonstrated equivalence, and the report
says so.

Each score also carries the gate (`passed`, decided by the policy) and how many
checks ran for the dimension, so that "no findings" can be told from "not looked at".
"""

from __future__ import annotations

from .model import (
    DOCUMENT_DIMENSIONS,
    ITEM_DIMENSIONS,
    Dimension,
    DimensionScore,
    Finding,
)
from .judge import WEIGHTS as JUDGE_WEIGHTS
from .policy import Policy
from .registry import CheckRegistry

#: Version of the rule set (checks + score formula). It goes into the report:
#: reports with different versions are not comparable. 2.0: checks for the
#: structured requirement format (EARS + story + Gherkin), support of a cited
#: turn without a quote, English lexicons. 2.1 (20/10/2026): links to another
#: artefact of the run are information; more Portuguese and English
#: verbs of effect, and filler ("as follows") is not an effect; a single
#: criterion, a generic role the sources support and vagueness the sources
#: themselves have are information, not defects.
RULESET_VERSION = "2.1"

#: Range of the ReqInOne questionnaire.
LIKERT_MIN = 1.0
LIKERT_MAX = 5.0


def to_likert(score: float) -> float:
    """Convert [0, 1] to [1, 5], the scale of the human raters."""
    clamped = max(0.0, min(1.0, score))
    return round(LIKERT_MIN + clamped * (LIKERT_MAX - LIKERT_MIN), 2)


def score_dimension(
    dimension: Dimension,
    findings: list[Finding],
    *,
    policy: Policy,
    registry: CheckRegistry,
    checks_run: list[str] | None = None,
) -> DimensionScore:
    """Score of one dimension from the findings that fell into it."""
    relevant = [f for f in findings if f.dimension is dimension]
    penalty = 0.0
    for finding in relevant:
        check = registry.get(finding.check_id)
        weight = check.weight if check else JUDGE_WEIGHTS.get(finding.check_id, 1.0)
        penalty += policy.penalty_for(finding.severity) * weight

    score = max(0.0, 1.0 - penalty)
    if checks_run is None:
        ran = len(registry.by_dimension(dimension))
    else:
        ran = sum(1 for check_id in checks_run if (c := registry.get(check_id)) and c.dimension is dimension)
    return DimensionScore(
        dimension=dimension,
        score=round(score, 4),
        likert=to_likert(score),
        findings=len(relevant),
        penalty=round(penalty, 4),
        passed=policy.dimension_passes(score, relevant),
        checks=ran,
    )


def score_item(
    findings: list[Finding],
    *,
    policy: Policy,
    registry: CheckRegistry,
    checks_run: list[str] | None = None,
) -> dict[Dimension, DimensionScore]:
    """The five item dimensions of one artefact."""
    return {
        dimension: score_dimension(dimension, findings, policy=policy, registry=registry, checks_run=checks_run)
        for dimension in ITEM_DIMENSIONS
    }


def score_document(
    findings: list[Finding],
    *,
    policy: Policy,
    registry: CheckRegistry,
    checks_run: list[str] | None = None,
) -> dict[Dimension, DimensionScore]:
    """The five document dimensions."""
    return {
        dimension: score_dimension(dimension, findings, policy=policy, registry=registry, checks_run=checks_run)
        for dimension in DOCUMENT_DIMENSIONS
    }
