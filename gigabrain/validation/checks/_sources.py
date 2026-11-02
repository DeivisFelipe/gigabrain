"""What the sources an artefact cites say, for the checks that must not ask for more.

Some checks ask the writer for something: a measurable criterion instead of a
vague word, a concrete role instead of "user", a second acceptance criterion.
When the source does not have it, the writer can only invent it, and the first
run with a real LLM showed it doing exactly that ("page load time under 2
seconds" from "adequate response time"). Those checks use these helpers to
tell a defect of the writer from a gap of the source.
"""

from __future__ import annotations

import re

from ..lexicons import ROLE_TERMS
from ..model import Artifact, SourceDocument
from ..registry import CheckContext
from ..text import find_terms


def cited_sources(artifact: Artifact, ctx: CheckContext) -> list[SourceDocument]:
    """The sources the artefact cites that came with the run."""
    found: list[SourceDocument] = []
    for ref in artifact.evidence:
        source = ctx.envelope.source(ref.source_id)
        if source is not None and source not in found:
            found.append(source)
    return found


_REQUIREMENT_ID = re.compile(r"\bR\d+\b")


def said_by_someone(source: SourceDocument) -> str:
    """The part of a source that someone said, leaving out what came from requirements.

    A related requirement, and the lines of an expert's knowledge that record saved
    requirements ("R1 (NFR) — ..."), were written by the system. Counting them would
    let a requirement vouch for itself: once saved, its own words are in the knowledge
    of the expert it cites (found by the tests on 20/10/2026).
    """
    if source.kind == "requirement":
        return ""
    if source.kind == "expert":
        return "\n".join(line for line in source.text.splitlines() if not _REQUIREMENT_ID.search(line))
    return source.text


def said_in_sources(term: str, artifact: Artifact, ctx: CheckContext) -> bool:
    """Whether someone in a cited source used the term (then the vagueness is inherited)."""
    return any(find_terms(said_by_someone(source), (term,)) for source in cited_sources(artifact, ctx))


def roles_in_sources(artifact: Artifact, ctx: CheckContext) -> list[str]:
    """Specific roles someone in the cited sources names (from the ROLE_TERMS lexicon), in order."""
    roles: list[str] = []
    for source in cited_sources(artifact, ctx):
        for term, _excerpt in find_terms(said_by_someone(source), ROLE_TERMS):
            if term not in roles:
                roles.append(term)
    return roles


def inherited_vagueness(term: str) -> tuple[str, str]:
    """(message, suggestion) of a vague term the source itself used."""
    return (
        f"The source itself is vague: '{term}' is what was said, with no measure.",
        "Ask the stakeholder for a measurable criterion. Do not invent a value the source does not give.",
    )
