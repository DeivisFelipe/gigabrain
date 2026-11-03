"""Understandability checks (dimension UNDERSTANDABLE).

Understandability is operationalised here by three measurable signs (sentence
length, atomicity and use of the glossary) and not by an imported readability
formula (Flesch and the like), which is calibrated for running prose and not for
requirements.

Atomicity sits in this dimension by design: a statement that hides two
requirements is the most common case of ambiguous reading at the item level, and
the "Small" and "Independent" of INVEST are already in the Digital Twin prompt.
"""

from __future__ import annotations

from typing import Iterable

from ..lexicons import CONJUNCTION_TERMS
from ..model import Artifact, Dimension, Finding, Level, Severity
from ..registry import CheckContext, item_check
from ..text import find_terms, normalize, sentences, word_count
from ._requires import requires_glossary


@item_check(
    id="readability.sentence_length",
    dimension=Dimension.UNDERSTANDABLE,
    severity=Severity.MINOR,
    description="No sentence of the artefact goes over the word limit.",
)
def sentence_length(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    limit = ctx.config.max_words_per_sentence
    # What is given in parts is not measured as one sentence. A story in three
    # parts and a Gherkin criterion read as long sentences only because of the way
    # they are rendered; the length of a criterion is the business of
    # verifiability.criterion_length.
    parts = [artifact.title, artifact.statement, artifact.rationale]
    if artifact.story_parts is None:
        parts.append(artifact.story)
    if not artifact.criteria:
        parts += artifact.acceptance_criteria
    for sentence in sentences("\n".join(p for p in parts if p)):
        count = word_count(sentence)
        if count > limit:
            yield Finding(
                check_id="readability.sentence_length",
                dimension=Dimension.UNDERSTANDABLE,
                severity=Severity.MINOR,
                level=Level.ITEM,
                target_id=artifact.id,
                locator="body",
                excerpt=sentence[:160],
                message=f"Sentence with {count} words (limit: {limit}).",
                suggestion="Split it into shorter sentences; a long requirement hides a double one.",
            )


@item_check(
    id="readability.atomicity",
    dimension=Dimension.UNDERSTANDABLE,
    severity=Severity.MAJOR,
    description="The statement is about one requirement only, with no conjunction joining two requests in the same sentence.",
    weight=1.2,
)
def atomicity(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    headline = artifact.headline()
    if not headline.strip():
        return
    for term, excerpt in find_terms(headline, CONJUNCTION_TERMS):
        yield Finding(
            check_id="readability.atomicity",
            dimension=Dimension.UNDERSTANDABLE,
            severity=Severity.MAJOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="statement",
            excerpt=excerpt,
            message=f"The statement seems to join more than one requirement ('{term.strip()}').",
            suggestion=(
                "Split it into two artefacts. A compound requirement cannot be estimated or "
                "tested on its own (the I and the S of INVEST)."
            ),
        )


@item_check(
    id="readability.glossary_usage",
    dimension=Dimension.UNDERSTANDABLE,
    severity=Severity.INFO,
    description="The domain terms the artefact uses are defined in the glossary of the run.",
    weight=0.5,
    requires="Skipped when the run has no glossary.",
    applies=requires_glossary,
)
def glossary_usage(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    # It only makes sense to ask for the definition of terms the glossary itself
    # already lists as domain terms; the opposite (guessing domain terms in the
    # text) would need an extractor we do not have and would produce noise.
    body = normalize(artifact.body_text())
    for entry in ctx.envelope.glossary:
        if entry.definition.strip() or normalize(entry.term) not in body:
            continue
        yield Finding(
            check_id="readability.glossary_usage",
            dimension=Dimension.UNDERSTANDABLE,
            severity=Severity.INFO,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="body",
            excerpt=entry.term,
            message=f"The artefact uses '{entry.term}', which is in the glossary without a definition.",
            suggestion="Ask the PO for the definition before closing the requirement.",
        )
