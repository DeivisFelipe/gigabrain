"""Conciseness checks (dimension CONCISENESS).

Conciseness is the easiest dimension to measure and the easiest to measure
wrong: a short text is not necessarily concise. What is measured here is excess
(an artefact far above the usual length, and the share of filler words), not
brevity itself.
"""

from __future__ import annotations

from typing import Iterable

from ..lexicons import FILLER_TERMS
from ..model import Dimension, Finding, Level, Severity
from ..registry import CheckContext, document_check
from ..text import find_terms, word_count


@document_check(
    id="conciseness.artifact_length",
    dimension=Dimension.CONCISENESS,
    severity=Severity.MINOR,
    description="No artefact goes over the configured maximum length.",
)
def artifact_length(ctx: CheckContext) -> Iterable[Finding]:
    limit = ctx.config.max_words_per_artifact
    for artifact in ctx.focus_artifacts():
        count = word_count(artifact.body_text())
        if count > limit:
            yield Finding(
                check_id="conciseness.artifact_length",
                dimension=Dimension.CONCISENESS,
                severity=Severity.MINOR,
                level=Level.DOCUMENT,
                target_id=artifact.id,
                message=f"Artefact with {count} words (limit: {limit}).",
                suggestion="A long artefact usually hides more than one requirement.",
            )


@document_check(
    id="conciseness.filler_terms",
    dimension=Dimension.CONCISENESS,
    severity=Severity.MINOR,
    description="The text does not lean on filler expressions.",
)
def filler_terms(ctx: CheckContext) -> Iterable[Finding]:
    for artifact in ctx.focus_artifacts():
        for term, excerpt in find_terms(artifact.body_text(), FILLER_TERMS):
            yield Finding(
                check_id="conciseness.filler_terms",
                dimension=Dimension.CONCISENESS,
                severity=Severity.MINOR,
                level=Level.DOCUMENT,
                target_id=artifact.id,
                excerpt=excerpt,
                message=f"Filler expression: '{term}'.",
                suggestion="Remove it; it does not change what the requirement asks for.",
            )

    document = ctx.envelope.document
    if document is None or ctx.focus:
        return
    for section in document.sections:
        total = word_count(section.text)
        if total < 20:
            continue
        hits = find_terms(section.text, FILLER_TERMS)
        ratio = len(hits) / max(total / 100.0, 1.0)  # occurrences per 100 words
        if hits and ratio > ctx.config.max_filler_ratio * 100:
            yield Finding(
                check_id="conciseness.filler_terms",
                dimension=Dimension.CONCISENESS,
                severity=Severity.MINOR,
                level=Level.DOCUMENT,
                target_id=section.id,
                excerpt=", ".join(t for t, _ in hits[:5]),
                message=f"Section '{section.title or section.id}' has {len(hits)} filler expressions in {total} words.",
                suggestion="Trim the section before closing the document.",
            )
