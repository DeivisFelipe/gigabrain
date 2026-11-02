"""Correctness checks: is the artefact grounded in its source? (dimension CORRECTNESS)

This is the core of the criticism of the self-declared regime, of which ReqInOne
is the documented instance: there, the model that writes the requirement is the
same one that says where it came from, and nobody checks. Here every origin an
agent declares is checked against material the Critic reads by itself.

Three kinds of claim are checked:

1. "It came from this source." The cited source must exist. With a literal
   quote, the quote must be in it; without one (the agents cite the turn or the
   conversation, not a passage), the statement must share enough content words
   with the cited text.
2. "It relates to that requirement." Every link target (refines, supersedes,
   depends_on, a new version of...) must exist in the repository. The
   repository silently ignores a link to a missing requirement; here it shows.
3. "It repeats that requirement." A duplicate claimed by an expert must look
   like the requirement it is said to repeat.

What these checks can and cannot decide:
- They CAN say whether the source exists and whether the words are there. That
  catches an invented citation and a citation attributed to the wrong source.
- They CANNOT say whether a passage that exists really *entails* the
  requirement (semantic implication). That judgement is left to the second tier
  and will show in the report marked as llm_judgment, never mixed
  with the deterministic result.
"""

from __future__ import annotations

from typing import Iterable

from ...database import LINK_KINDS
from ..lexicons import STOPWORDS
from ..model import Artifact, ArtifactKind, Dimension, Finding, Level, Severity
from ..ports import NullTraceabilityProvider
from ..registry import CheckContext, item_check
from ..text import containment, coverage, jaccard, language_of, normalize
from ._requires import requires_sources

#: Claims carried as links that the repository does not store as links.
CLAIM_KINDS = ("base", "duplicates")


@item_check(
    id="grounding.evidence_present",
    dimension=Dimension.CORRECTNESS,
    severity=Severity.MAJOR,
    description="The artefact declares at least one source that supports it.",
    weight=1.5,
    requires="Required when config.require_evidence is on; does not apply to questions.",
)
def evidence_present(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    if artifact.kind is ArtifactKind.QUESTION or not ctx.config.require_evidence:
        return
    if not artifact.evidence:
        yield Finding(
            check_id="grounding.evidence_present",
            dimension=Dimension.CORRECTNESS,
            severity=Severity.MAJOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="evidence",
            message="The artefact declares no source.",
            suggestion=(
                "Point to the turn of the meeting, the conversation or the document the "
                "requirement came from. Without it, elicitation cannot be told from invention."
            ),
        )


@item_check(
    id="grounding.evidence_resolvable",
    dimension=Dimension.CORRECTNESS,
    severity=Severity.BLOCKER,
    description="Every cited source exists among the sources of the run.",
    weight=1.5,
    requires="Skipped when the run has no sources: the right answer then is 'I do not know', not 'it does not exist'.",
    applies=requires_sources,
)
def evidence_resolvable(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    for index, ref in enumerate(artifact.evidence, start=1):
        if ctx.envelope.source(ref.source_id) is None:
            yield Finding(
                check_id="grounding.evidence_resolvable",
                dimension=Dimension.CORRECTNESS,
                severity=Severity.BLOCKER,
                level=Level.ITEM,
                target_id=artifact.id,
                locator=f"evidence[{index}].source_id",
                excerpt=ref.source_id,
                message=f"The cited source does not exist: '{ref.source_id}'.",
                suggestion="Either the source was left out of the run, or the agent cited an identifier it made up.",
            )


@item_check(
    id="grounding.quote_supported",
    dimension=Dimension.CORRECTNESS,
    severity=Severity.BLOCKER,
    description="A quoted passage really appears in the source it is attributed to (normalised text, configurable tolerance).",
    weight=2.0,
    requires="Looks at references that carry a quote and whose source exists; skipped when the run has no sources.",
    applies=requires_sources,
)
def quote_supported(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    threshold = ctx.config.min_quote_containment
    for index, ref in enumerate(artifact.evidence, start=1):
        source = ctx.envelope.source(ref.source_id)
        quote = (ref.quote or "").strip()
        if source is None or not quote:
            continue  # a missing source is reported by evidence_resolvable; no quote, by statement_supported
        score = containment(quote, source.text)
        if score < threshold:
            yield Finding(
                check_id="grounding.quote_supported",
                dimension=Dimension.CORRECTNESS,
                severity=Severity.BLOCKER,
                level=Level.ITEM,
                target_id=artifact.id,
                locator=f"evidence[{index}].quote",
                excerpt=quote[:160],
                message=(
                    f"The quote was not found in '{ref.source_id}' "
                    f"(containment {score:.0%}, minimum {threshold:.0%})."
                ),
                suggestion=(
                    "A self-declared quote that does not match the source is the classic "
                    "symptom of hallucination. Quote the real passage of the source."
                ),
            )


@item_check(
    id="grounding.statement_supported",
    dimension=Dimension.CORRECTNESS,
    severity=Severity.MAJOR,
    description=(
        "When the artefact cites sources without quoting them, its statement shares enough "
        "content words with the cited text."
    ),
    weight=2.0,
    requires="Looks at artefacts whose references carry no quote; skipped when the run has no sources.",
    applies=requires_sources,
)
def statement_supported(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    claim = artifact.headline().strip()
    if not claim or any((ref.quote or "").strip() for ref in artifact.evidence):
        return
    cited = [ctx.envelope.source(ref.source_id) for ref in artifact.evidence]
    cited = [source for source in cited if source is not None]
    if not cited:
        return  # nothing resolvable: reported by evidence_present / evidence_resolvable
    threshold = ctx.config.min_statement_support
    said = "\n".join(source.text for source in cited)
    score = coverage(claim, said, stopwords=STOPWORDS)
    if score < threshold:
        names = ", ".join(source.id for source in cited[:5])
        written, source_language = language_of(claim), language_of(said)
        if written and source_language and written != source_language:
            # Words of two languages cannot be compared, and "keep the words" does not tell the agent what happened.
            yield Finding(
                check_id="grounding.statement_supported",
                dimension=Dimension.CORRECTNESS,
                severity=Severity.MAJOR,
                level=Level.ITEM,
                target_id=artifact.id,
                locator="statement",
                excerpt=claim[:160],
                message=(
                    f"The statement is in {written} and its source ({names}) is in {source_language}, "
                    "so it cannot be compared with what was said."
                ),
                suggestion=f"Write the requirement in {source_language}, the language of whoever asked for it.",
            )
            return
        yield Finding(
            check_id="grounding.statement_supported",
            dimension=Dimension.CORRECTNESS,
            severity=Severity.MAJOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="statement",
            excerpt=claim[:160],
            message=(
                f"The statement is not supported by the cited source ({names}): "
                f"support {score:.0%}, minimum {threshold:.0%}."
            ),
            suggestion=(
                "Keep the words of whoever asked for it, or cite the source that really says it. "
                "A requirement that shares almost nothing with its source was not elicited from it."
            ),
        )


@item_check(
    id="grounding.reason_present",
    dimension=Dimension.CORRECTNESS,
    severity=Severity.INFO,
    description="Each quoted source comes with the reason why it supports the requirement (the 'Trace to Source' of ReqInOne).",
    weight=0.5,
    requires="Looks at references that carry a quote.",
)
def reason_present(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    for index, ref in enumerate(artifact.evidence, start=1):
        if (ref.quote or "").strip() and not (ref.reason or "").strip():
            yield Finding(
                check_id="grounding.reason_present",
                dimension=Dimension.CORRECTNESS,
                severity=Severity.INFO,
                level=Level.ITEM,
                target_id=artifact.id,
                locator=f"evidence[{index}].reason",
                excerpt=ref.source_id,
                message=f"The reference to '{ref.source_id}' gives no reason.",
                suggestion="Record why the passage leads to this requirement and not to another.",
            )


@item_check(
    id="grounding.links_resolvable",
    dimension=Dimension.CORRECTNESS,
    severity=Severity.MAJOR,
    description=(
        "Every requirement the artefact links to exists, in the run or in the repository, and the link "
        "kind is a known one. A link to another artefact of the run (by id or by title) that the "
        "repository does not have yet is real, and is reported only as information."
    ),
    weight=1.5,
    requires="Looks at artefacts that declare links; without a traceability provider it says it could not check, instead of failing.",
)
def links_resolvable(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    unknown: list[str] = []
    no_provider = isinstance(ctx.trace, NullTraceabilityProvider)

    def finding(severity: Severity, locator: str, excerpt: str, message: str, suggestion: str) -> Finding:
        return Finding(check_id="grounding.links_resolvable", dimension=Dimension.CORRECTNESS, severity=severity,
                       level=Level.ITEM, target_id=artifact.id, locator=locator, excerpt=excerpt,
                       message=message, suggestion=suggestion)

    for index, link in enumerate(artifact.links, start=1):
        locator = f"links[{index}]"
        if link.kind not in LINK_KINDS and link.kind not in CLAIM_KINDS:
            yield finding(Severity.MINOR, locator, link.kind, f"Unknown link kind: '{link.kind}'.",
                          f"Use one of: {', '.join(LINK_KINDS)}. The repository ignores any other.")
        in_run = _artifact_in_run(ctx, link.target)
        if not link.target or link.target == artifact.id or (in_run is not None and in_run.id == artifact.id):
            yield finding(Severity.MAJOR, locator, link.target or "(empty)",
                          "The link points to no requirement or to the artefact itself.",
                          "Name the other requirement the relation is with.")
            continue
        if ctx.trace.knows(link.target):
            continue
        if in_run is not None:
            if no_provider or ctx.trace.knows(in_run.id):
                continue
            # A relation between two requirements proposed together (P2 depends on P1). It is
            # real, and returning it made the agent delete it. Saving only keeps links between
            # saved requirements, so it is said here instead of being lost in silence.
            yield finding(Severity.INFO, locator, link.target,
                          f"The link points to {in_run.id}, which is in this run but not in the repository yet.",
                          "Keep it: it records a real relation. The repository only keeps links between saved "
                          "requirements, so record it again once both are saved.")
            continue
        if no_provider:
            unknown.append(f"{link.kind} {link.target}")
            continue
        yield finding(Severity.MAJOR, locator, link.target,
                      f"The artefact declares '{link.kind} {link.target}', and {link.target} does not exist.",
                      "Link to a requirement that exists. The repository drops this link without saying so.")

    if unknown:
        yield finding(Severity.INFO, "links", ", ".join(unknown[:10]),
                      f"{len(unknown)} link(s) could not be checked: no traceability provider is plugged in.",
                      "Plug the repository in through the TraceabilityProvider port for this check to count.")


def _artifact_in_run(ctx: CheckContext, target: str) -> Artifact | None:
    """The artefact of this run a link points to, by id or, as agents write before there is an id, by title."""
    if not target:
        return None
    found = ctx.envelope.artifact(target)
    if found is not None:
        return found
    key = normalize(target)
    return next((a for a in ctx.envelope.artifacts if a.title and normalize(a.title) == key), None)


@item_check(
    id="grounding.duplicate_claim",
    dimension=Dimension.CORRECTNESS,
    severity=Severity.MAJOR,
    description="A requirement said to repeat another one really looks like it.",
    weight=1.5,
    requires="Looks at artefacts with a 'duplicates' claim whose target is in the run.",
)
def duplicate_claim(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    threshold = ctx.config.min_duplicate_claim_similarity
    for index, link in enumerate(artifact.links, start=1):
        if link.kind != "duplicates":
            continue
        target = ctx.envelope.artifact(link.target)
        if target is None:
            continue  # its existence is the business of links_resolvable
        score = jaccard(artifact.headline(), target.headline(), stopwords=STOPWORDS)
        if score < threshold:
            yield Finding(
                check_id="grounding.duplicate_claim",
                dimension=Dimension.CORRECTNESS,
                severity=Severity.MAJOR,
                level=Level.ITEM,
                target_id=artifact.id,
                locator=f"links[{index}]",
                excerpt=f"{artifact.headline()[:80]} // {target.headline()[:80]}",
                message=(
                    f"The artefact is said to repeat {link.target}, but the two share little "
                    f"(similarity {score:.0%}, minimum {threshold:.0%})."
                ),
                suggestion="Check which requirement it repeats, or keep it: it may be a new requirement.",
            )
