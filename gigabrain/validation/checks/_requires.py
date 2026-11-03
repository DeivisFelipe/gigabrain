"""Applicability preconditions of the checks.

Each function returns `None` when the check can run, or the reason why it cannot,
in words that go straight into the report.

This protects a rule that holds for the whole layer: when the
material a check needs did not come with the run, the right answer is **I do not
know**, never **it does not exist**. Failing an artefact because the run did not
bring the sources would blame the requirement for a gap of whoever built the input.
"""

from __future__ import annotations

from ..registry import CheckContext


def requires_sources(ctx: CheckContext) -> str | None:
    if not ctx.envelope.sources:
        return "the run has no sources: there is nothing to compare with"
    return None


def requires_needs(ctx: CheckContext) -> str | None:
    if not any(s.kind == "need" for s in ctx.envelope.sources):
        return "the run has no sources of kind 'need'"
    return None


def requires_document(ctx: CheckContext) -> str | None:
    if ctx.envelope.document is None:
        return "the run has no assembled document"
    return None


def requires_glossary(ctx: CheckContext) -> str | None:
    if not ctx.envelope.glossary:
        return "the run has no glossary"
    return None


def requires_artifacts(ctx: CheckContext) -> str | None:
    if not ctx.envelope.artifacts:
        return "the run has no artefacts"
    return None


def requires_whole_set(ctx: CheckContext) -> str | None:
    """For checks about the specification as a whole, which say nothing about one candidate."""
    if ctx.focus:
        return "focused run: this check looks at the whole specification"
    return None


def all_of(*preconditions):
    """A precondition that holds when every one of the given ones holds; the first reason wins."""
    def applies(ctx: CheckContext) -> str | None:
        for precondition in preconditions:
            reason = precondition(ctx)
            if reason:
                return reason
        return None
    return applies


def requires_kind(kind: str):
    def applies(ctx: CheckContext) -> str | None:
        if not any(s.kind == kind for s in ctx.envelope.sources):
            return f"the run has no sources of kind '{kind}'"
        return None
    return applies
