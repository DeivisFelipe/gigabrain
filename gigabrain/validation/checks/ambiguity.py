"""Unambiguity checks (dimension UNAMBIGUOUS).

Ambiguity is the defect ReqInOne itself reports as remaining in every model it
evaluated ("appropriate", "user-friendly"), and the defect the Digital Twin
prompt asks the agent to point out. Here the check no longer depends on the
generator: it is made from outside, against a versioned lexicon
(validation/lexicons.py).

The honest limit of the approach: it is lexical detection, not semantic. It gets
the frequent case right (a subjective adjective without a metric) and misses
structural ambiguity (scope of a negation, what a pronoun refers to). Those cases
are left to the second tier, the LLM judge.
"""

from __future__ import annotations

from typing import Iterable

from ..lexicons import (
    GENERIC_PERSONAS,
    OPTIONALITY_TERMS,
    OVERGENERALIZATION_TERMS,
    UNQUANTIFIED_TERMS,
    VAGUE_TERMS,
)
from ..model import Artifact, ArtifactKind, Dimension, Finding, Level, Severity
from ..registry import CheckContext, item_check
from ..text import find_terms, has_measurable_quantity, has_number, normalize
from ._sources import inherited_vagueness, roles_in_sources, said_in_sources


def verifiable_units(artifact: Artifact) -> list[tuple[str, str]]:
    """The parts of an artefact that must be verifiable, as (locator, text).

    The statement and each acceptance criterion. The story only counts when it
    is the requirement itself (a user_story artefact): in a structured
    requirement, the benefit of the story is where a subjective intent belongs.
    """
    units: list[tuple[str, str]] = []
    if artifact.statement.strip():
        units.append(("statement", artifact.statement))
    elif artifact.story.strip():
        units.append(("story", artifact.story))
    for index, criterion in enumerate(artifact.acceptance_criteria, start=1):
        if criterion.strip():
            units.append((f"acceptance_criteria[{index}]", criterion))
    return units


def vague_hits(text: str) -> list[tuple[str, str]]:
    """Vague terms of a text, unless a number goes with them.

    "fast" is vague; "fast, within 2 seconds" is not. This is the rule of the
    project's format check (requirement_format.vague_terms), applied to one
    sentence at a time instead of to the whole requirement.
    """
    if has_number(text) or has_measurable_quantity(text):
        return []
    return find_terms(text, VAGUE_TERMS)


@item_check(
    id="ambiguity.vague_terms",
    dimension=Dimension.UNAMBIGUOUS,
    severity=Severity.MAJOR,
    description=(
        "The statement does not use subjective words without a measurable criterion "
        "(fast, easy, intuitive, user-friendly...)."
    ),
    weight=1.5,
)
def vague_terms(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    reported: set[str] = set()
    for locator, text in verifiable_units(artifact):
        if locator.startswith("acceptance_criteria"):
            continue  # reported by verifiability.criterion_not_vague, in its own dimension
        for term, excerpt in vague_hits(text):
            if term in reported:
                continue
            reported.add(term)
            if said_in_sources(term, artifact, ctx):  # inherited: a gap of the source
                message, suggestion = inherited_vagueness(term)
                yield Finding(check_id="ambiguity.vague_terms", dimension=Dimension.UNAMBIGUOUS,
                              severity=Severity.INFO, level=Level.ITEM, target_id=artifact.id,
                              locator=locator, excerpt=excerpt, message=message, suggestion=suggestion)
                continue
            yield Finding(
                check_id="ambiguity.vague_terms",
                dimension=Dimension.UNAMBIGUOUS,
                severity=Severity.MAJOR,
                level=Level.ITEM,
                target_id=artifact.id,
                locator=locator,
                excerpt=excerpt,
                message=f"Subjective term without a measurable criterion: '{term}'.",
                suggestion=(
                    f"Replace '{term}' with something verifiable (a value, a unit, a limit) "
                    "or move the intent to the benefit of the story."
                ),
            )


@item_check(
    id="ambiguity.optionality",
    dimension=Dimension.UNAMBIGUOUS,
    severity=Severity.MAJOR,
    description="The artefact does not use words that make the requirement optional and so impossible to decide.",
)
def optionality(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    for term, excerpt in find_terms(artifact.body_text(), OPTIONALITY_TERMS):
        yield Finding(
            check_id="ambiguity.optionality",
            dimension=Dimension.UNAMBIGUOUS,
            severity=Severity.MAJOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="body",
            excerpt=excerpt,
            message=f"An optional wording makes the requirement undecidable: '{term}'.",
            suggestion=(
                "Decide: either the behaviour is required (and becomes an acceptance "
                "criterion) or it is not a requirement and leaves the artefact."
            ),
        )


@item_check(
    id="ambiguity.unquantified",
    dimension=Dimension.UNAMBIGUOUS,
    severity=Severity.MINOR,
    description="Vague quantifiers ('many', 'large', 'frequently') come with a number or a unit.",
)
def unquantified(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    text = artifact.body_text()
    hits = find_terms(text, UNQUANTIFIED_TERMS)
    if not hits:
        return
    # If the artefact already has a measurable quantity, the quantifier is most
    # likely just going along with the number: not worth a finding.
    if has_measurable_quantity(text):
        return
    for term, excerpt in hits:
        if has_number(excerpt):
            continue
        if said_in_sources(term, artifact, ctx):  # inherited: a gap of the source
            message, suggestion = inherited_vagueness(term)
            yield Finding(check_id="ambiguity.unquantified", dimension=Dimension.UNAMBIGUOUS,
                          severity=Severity.INFO, level=Level.ITEM, target_id=artifact.id,
                          locator="body", excerpt=excerpt, message=message, suggestion=suggestion)
            continue
        yield Finding(
            check_id="ambiguity.unquantified",
            dimension=Dimension.UNAMBIGUOUS,
            severity=Severity.MINOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="body",
            excerpt=excerpt,
            message=f"Quantifier without a quantity: '{term}'.",
            suggestion=f"Replace '{term}' with a number and a unit.",
        )


@item_check(
    id="ambiguity.overgeneralization",
    dimension=Dimension.UNAMBIGUOUS,
    severity=Severity.MINOR,
    description="The artefact avoids absolutes ('always', 'never', 'all cases').",
)
def overgeneralization(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    for term, excerpt in find_terms(artifact.body_text(), OVERGENERALIZATION_TERMS):
        yield Finding(
            check_id="ambiguity.overgeneralization",
            dimension=Dimension.UNAMBIGUOUS,
            severity=Severity.MINOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="body",
            excerpt=excerpt,
            message=f"Absolute generalisation: '{term}'.",
            suggestion="State the conditions under which the behaviour holds.",
        )


def _role(artifact: Artifact) -> str:
    """The role of the story, from its structured form or from the sentence."""
    if artifact.story_parts is not None:
        return normalize(artifact.story_parts.role)
    canon = normalize(artifact.story, keep_punct=True)
    for opening in ("as an ", "as a ", "como "):
        if canon.startswith(opening):
            return normalize(canon[len(opening):].split(",", 1)[0])
    return ""


@item_check(
    id="ambiguity.generic_persona",
    dimension=Dimension.UNAMBIGUOUS,
    severity=Severity.MINOR,
    description=(
        "The role of the story is specific enough to know who is meant ('user' alone says nothing), "
        "when a cited source names a specific role. When none does, 'user' is all the source "
        "supports and the finding is only information."
    ),
    requires="Applies to artefacts with a user story.",
)
def generic_persona(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    if artifact.kind not in (ArtifactKind.USER_STORY, ArtifactKind.REQUIREMENT):
        return
    role = _role(artifact)
    if not role:
        return
    if role not in {normalize(generic) for generic in GENERIC_PERSONAS}:
        return
    named = roles_in_sources(artifact, ctx)
    if not named:
        yield Finding(
            check_id="ambiguity.generic_persona",
            dimension=Dimension.UNAMBIGUOUS,
            severity=Severity.INFO,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="story.role",
            excerpt=role,
            message=f"Generic role: '{role}'. The cited sources name no specific role, so this is all they support.",
            suggestion="Ask the stakeholder who uses it. Do not invent a persona.",
        )
        return
    yield Finding(
        check_id="ambiguity.generic_persona",
        dimension=Dimension.UNAMBIGUOUS,
        severity=Severity.MINOR,
        level=Level.ITEM,
        target_id=artifact.id,
        locator="story.role",
        excerpt=role,
        message=f"Generic role: '{role}', and the source names: {', '.join(named[:3])}.",
        suggestion="Use the role the source names: it is what defines value.",
    )
