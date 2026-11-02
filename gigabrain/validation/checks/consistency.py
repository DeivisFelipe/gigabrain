"""Internal consistency checks (dimension INTERNAL_CONSISTENCY).

Consistency is a property of the set, not of the item: two requirements can each
be impeccable and still contradict each other. That is why these checks run over
the requirements taken together, or over one candidate against the repository.

About the contradiction detectors: general semantic contradiction cannot be
decided by a rule. What is done here is conservative and declared:

- negation: pairs of statements about the same subject where one is negated and
  the other is not ("the system shall notify" / "the system shall not notify");
- value: pairs of statements that are the same except for a number ("within 2
  seconds" / "within 4 seconds").

Both miss the indirect contradiction. A finding comes out as MINOR and worded as
a suspicion, not as a verdict: the decision is the PO's, or the second tier's.
A pair the agents already linked (conflicts_with, supersedes, refines...) is not
reported again: the relation is acknowledged.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Iterable

from ..lexicons import NEGATION_RE, STOPWORDS
from ..model import Artifact, Dimension, Finding, Level, Severity
from ..registry import CheckContext, document_check
from ..text import normalize, token_set
from ._requires import requires_glossary

#: Minimum overlap of content words for two statements to be "about the same thing".
SAME_SUBJECT = 0.60
#: Minimum overlap, numbers aside, for two statements to be the same one with another value.
SAME_BUT_VALUE = 0.80

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


@document_check(
    id="consistency.unique_ids",
    dimension=Dimension.INTERNAL_CONSISTENCY,
    severity=Severity.BLOCKER,
    description="Each artefact has a unique identifier.",
    weight=1.5,
)
def unique_ids(ctx: CheckContext) -> Iterable[Finding]:
    counts = Counter(a.id for a in ctx.envelope.artifacts)
    for artifact_id, count in sorted(counts.items()):
        if count > 1 and ctx.in_focus(artifact_id):
            yield Finding(
                check_id="consistency.unique_ids",
                dimension=Dimension.INTERNAL_CONSISTENCY,
                severity=Severity.BLOCKER,
                level=Level.DOCUMENT,
                target_id=artifact_id,
                message=f"Identifier used {count} times: '{artifact_id}'.",
                suggestion="Without a unique identifier there is no traceability and no versioning.",
            )


def negated(text: str) -> bool:
    return bool(NEGATION_RE.search(normalize(text)))


def linked(left: Artifact, right: Artifact) -> bool:
    """Whether the agents already declared a relation between the two."""
    return right.id in left.linked_ids() or left.id in right.linked_ids()


def _content_key(artifact: Artifact) -> frozenset[str]:
    """Lexical core of the statement, without support words, negation or numbers."""
    tokens = token_set(artifact.headline(), stopwords=STOPWORDS)
    return frozenset(t for t in tokens if not NEGATION_RE.fullmatch(t) and not t.isdigit())


def _overlap(left: frozenset[str], right: frozenset[str]) -> float:
    return len(left & right) / len(left | right) if left and right else 0.0


@document_check(
    id="consistency.negation_conflict",
    dimension=Dimension.INTERNAL_CONSISTENCY,
    severity=Severity.MINOR,
    description="No pair of requirements about the same subject has one stating and the other denying the same behaviour.",
    requires="A conservative and deliberately incomplete detector: it only finds literal contradiction by negation, and reports it as a suspicion.",
)
def negation_conflict(ctx: CheckContext) -> Iterable[Finding]:
    artifacts = [a for a in ctx.envelope.artifacts if a.headline().strip()]
    for left, right in ctx.pairs(artifacts):
        if negated(left.headline()) == negated(right.headline()) or linked(left, right):
            continue
        overlap = _overlap(_content_key(left), _content_key(right))
        if overlap < SAME_SUBJECT:
            continue
        yield Finding(
            check_id="consistency.negation_conflict",
            dimension=Dimension.INTERNAL_CONSISTENCY,
            severity=Severity.MINOR,
            level=Level.DOCUMENT,
            target_id=f"{left.id}|{right.id}",
            excerpt=f"{left.headline()[:80]} // {right.headline()[:80]}",
            message=(
                f"Possible contradiction between '{left.id}' and '{right.id}': same subject "
                f"({overlap:.0%} overlap), one negated and the other not."
            ),
            suggestion="Confirm with the PO which one holds, or state the condition that separates the two cases.",
        )


@document_check(
    id="consistency.value_conflict",
    dimension=Dimension.INTERNAL_CONSISTENCY,
    severity=Severity.MINOR,
    description="No pair of requirements states the same thing with a different value.",
    requires="Conservative: only pairs that are the same statement except for the numbers, and not already linked.",
)
def value_conflict(ctx: CheckContext) -> Iterable[Finding]:
    artifacts = [a for a in ctx.envelope.artifacts if _NUMBER_RE.search(a.headline())]
    for left, right in ctx.pairs(artifacts):
        if linked(left, right) or negated(left.headline()) != negated(right.headline()):
            continue
        numbers_left = set(_NUMBER_RE.findall(left.headline()))
        numbers_right = set(_NUMBER_RE.findall(right.headline()))
        if numbers_left == numbers_right:
            continue
        overlap = _overlap(_content_key(left), _content_key(right))
        if overlap < SAME_BUT_VALUE:
            continue
        yield Finding(
            check_id="consistency.value_conflict",
            dimension=Dimension.INTERNAL_CONSISTENCY,
            severity=Severity.MINOR,
            level=Level.DOCUMENT,
            target_id=f"{left.id}|{right.id}",
            excerpt=f"{left.headline()[:80]} // {right.headline()[:80]}",
            message=(
                f"'{left.id}' and '{right.id}' look like the same requirement with different values "
                f"({', '.join(sorted(numbers_left)) or 'none'} x {', '.join(sorted(numbers_right)) or 'none'})."
            ),
            suggestion="If one replaces the other, link them (supersedes); if both hold, state what tells them apart.",
        )


@document_check(
    id="consistency.terminology_drift",
    dimension=Dimension.INTERNAL_CONSISTENCY,
    severity=Severity.MINOR,
    description="A concept of the glossary is always called by the same name.",
    requires="Skipped when the run has no glossary with declared synonyms.",
    applies=requires_glossary,
)
def terminology_drift(ctx: CheckContext) -> Iterable[Finding]:
    # Synonyms are declared in the glossary by a definition that starts with
    # "synonym of X" (or "sinônimo de X"). Without that there is no way to know
    # that two names mean the same thing, and guessing would produce noise.
    canonical: dict[str, str] = {}
    for entry in ctx.envelope.glossary:
        match = re.match(r"^(?:synonym of|sinonimo de) (.+)$", normalize(entry.definition))
        if match:
            canonical[normalize(entry.term)] = match.group(1).strip()
    if not canonical:
        return

    used: dict[str, set[str]] = defaultdict(set)
    for artifact in ctx.focus_artifacts():
        body = normalize(artifact.body_text())
        for alias, target in canonical.items():
            if re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", body):
                used[target].add(artifact.id)

    for target, artifact_ids in sorted(used.items()):
        yield Finding(
            check_id="consistency.terminology_drift",
            dimension=Dimension.INTERNAL_CONSISTENCY,
            severity=Severity.MINOR,
            level=Level.DOCUMENT,
            target_id=",".join(sorted(artifact_ids)),
            excerpt=target,
            message=f"A synonym of '{target}' is used in {len(artifact_ids)} artefact(s) instead of the canonical term.",
            suggestion=f"Use '{target}', as recorded in the glossary.",
        )
