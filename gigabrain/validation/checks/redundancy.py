"""Non-redundancy checks (dimension NON_REDUNDANCY).

Redundancy is the defect ReqInOne measures and where GPT-4o does worst. Here it
is detected by token-set similarity (Jaccard over normalised text with plurals
reduced), computed in pairs.

Two thresholds, on purpose: above `duplicate_threshold` the pair is treated as a
duplicate (MAJOR); between `near_duplicate_threshold` and it, as an overlap to
review (MINOR). Keeping the two apart avoids turning "requirements that look
alike because they are about the same module" into an error.

Three kinds of pair are left to other checks or to nobody: a pair where one
statement negates the other (a contradiction, see consistency.negation_conflict),
a pair that differs only in a value (consistency.value_conflict), and a pair the
agents already linked (the overlap is explained by the declared relation).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from ..lexicons import STOPWORDS
from ..model import Dimension, Finding, Level, Severity
from ..registry import CheckContext, document_check
from ..text import jaccard, normalize
from .consistency import SAME_BUT_VALUE, _NUMBER_RE, _content_key, _overlap, linked, negated


def _same_but_value(left, right) -> bool:
    numbers_left = set(_NUMBER_RE.findall(left.headline()))
    numbers_right = set(_NUMBER_RE.findall(right.headline()))
    return numbers_left != numbers_right and _overlap(_content_key(left), _content_key(right)) >= SAME_BUT_VALUE


@document_check(
    id="redundancy.near_duplicate",
    dimension=Dimension.NON_REDUNDANCY,
    severity=Severity.MAJOR,
    description="No pair of requirements has almost identical statements, measured by token similarity.",
    weight=1.5,
)
def near_duplicate(ctx: CheckContext) -> Iterable[Finding]:
    high = ctx.config.duplicate_threshold
    low = ctx.config.near_duplicate_threshold
    artifacts = [a for a in ctx.envelope.artifacts if a.headline().strip()]
    for left, right in ctx.pairs(artifacts):
        if negated(left.headline()) != negated(right.headline()) or linked(left, right) or _same_but_value(left, right):
            continue
        score = jaccard(left.headline(), right.headline(), stopwords=STOPWORDS)
        if score >= high:
            yield Finding(
                check_id="redundancy.near_duplicate",
                dimension=Dimension.NON_REDUNDANCY,
                severity=Severity.MAJOR,
                level=Level.DOCUMENT,
                target_id=f"{left.id}|{right.id}",
                excerpt=f"{left.headline()[:80]} // {right.headline()[:80]}",
                message=f"'{left.id}' and '{right.id}' are practically the same requirement (similarity {score:.0%}).",
                suggestion="Merge the two or state what tells one from the other.",
            )
        elif score >= low:
            yield Finding(
                check_id="redundancy.near_duplicate",
                dimension=Dimension.NON_REDUNDANCY,
                severity=Severity.MINOR,
                level=Level.DOCUMENT,
                target_id=f"{left.id}|{right.id}",
                excerpt=f"{left.headline()[:80]} // {right.headline()[:80]}",
                message=f"'{left.id}' and '{right.id}' overlap (similarity {score:.0%}).",
                suggestion="Check whether one is a particular case of the other; if so, link them (refines).",
            )


@document_check(
    id="redundancy.duplicate_criteria",
    dimension=Dimension.NON_REDUNDANCY,
    severity=Severity.MINOR,
    description="The same acceptance criterion is not repeated in different artefacts.",
)
def duplicate_criteria(ctx: CheckContext) -> Iterable[Finding]:
    owners: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for artifact in ctx.envelope.artifacts:
        for criterion in artifact.acceptance_criteria:
            key = " ".join(sorted(normalize(criterion).split()))
            if key and artifact.id not in {owner for owner, _ in owners[key]}:
                owners[key].append((artifact.id, criterion))
    for entries in owners.values():
        first, _ = entries[0]
        for other, criterion in entries[1:]:
            if ctx.in_focus(first) or ctx.in_focus(other):
                yield Finding(
                    check_id="redundancy.duplicate_criteria",
                    dimension=Dimension.NON_REDUNDANCY,
                    severity=Severity.MINOR,
                    level=Level.DOCUMENT,
                    target_id=f"{first}|{other}",
                    excerpt=criterion.strip()[:120],
                    message=f"Identical acceptance criterion in '{first}' and '{other}'.",
                    suggestion="A repeated criterion usually means a repeated requirement, or a criterion too generic to test anything.",
                )
