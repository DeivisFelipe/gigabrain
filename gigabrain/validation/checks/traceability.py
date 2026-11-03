"""Traceability checks (dimension TRACEABILITY).

The boundary of responsibility, made explicit:

- **keeping** the links, the history of versions and the impact analysis is the
  job of the Requirements Repository;
- **verifying** that what the agents claim holds against it is the job of validation.

Hence the shape of these checks: they measure coverage over what is in the run
and, for what depends on the history of the project, they ask through the
TraceabilityProvider port. When the port is the neutral one, a check says it
could not look: it never fails an artefact because nothing was plugged in.

The links an agent declares on one artefact (refines R3, supersedes R1...) are
checked at the item level, by grounding.links_resolvable: a link to a
requirement that does not exist is a false claim of that artefact.
"""

from __future__ import annotations

from typing import Iterable

from ..model import ArtifactKind, Dimension, Finding, Level, Severity
from ..ports import NullTraceabilityProvider
from ..registry import CheckContext, document_check
from ._requires import all_of, requires_needs, requires_sources, requires_whole_set


@document_check(
    id="traceability.forward_coverage",
    dimension=Dimension.TRACEABILITY,
    severity=Severity.MAJOR,
    description="Share of requirements that point to at least one existing source (requirement -> origin).",
    weight=2.0,
    requires="Skipped when the run has no sources.",
    applies=requires_sources,
)
def forward_coverage(ctx: CheckContext) -> Iterable[Finding]:
    artifacts = [a for a in ctx.focus_artifacts() if a.kind is not ArtifactKind.QUESTION]
    if not artifacts:
        return
    traced = [
        a for a in artifacts
        if any(ctx.envelope.source(ref.source_id) is not None for ref in a.evidence)
    ]
    coverage = len(traced) / len(artifacts)
    minimum = ctx.config.min_forward_coverage
    if coverage >= minimum:
        return
    untraced = [a.id for a in artifacts if a not in traced]
    yield Finding(
        check_id="traceability.forward_coverage",
        dimension=Dimension.TRACEABILITY,
        severity=Severity.MAJOR,
        level=Level.DOCUMENT,
        target_id=",".join(untraced[:10]),
        message=(
            f"Requirement -> source coverage of {coverage:.0%} (minimum {minimum:.0%}); "
            f"{len(untraced)} requirement(s) without a valid trace."
        ),
        suggestion=(
            "A requirement without a verifiable origin cannot be defended before the client "
            "nor reassessed when the need changes."
        ),
    )


@document_check(
    id="traceability.backward_coverage",
    dimension=Dimension.TRACEABILITY,
    severity=Severity.MAJOR,
    description="Share of the needs that some requirement used (origin -> requirement).",
    weight=1.5,
    requires="Looks at sources of kind 'need', over the whole specification; skipped when there are none.",
    applies=all_of(requires_whole_set, requires_needs),
)
def backward_coverage(ctx: CheckContext) -> Iterable[Finding]:
    needs = [s for s in ctx.envelope.sources if s.kind == "need"]
    referenced = {ref.source_id for artifact in ctx.envelope.artifacts for ref in artifact.evidence}
    missing = [n.id for n in needs if n.id not in referenced]
    coverage = 1 - len(missing) / len(needs)
    minimum = ctx.config.min_backward_coverage
    if coverage >= minimum:
        return
    yield Finding(
        check_id="traceability.backward_coverage",
        dimension=Dimension.TRACEABILITY,
        severity=Severity.MAJOR,
        level=Level.DOCUMENT,
        target_id=",".join(missing[:10]),
        message=(
            f"Source -> requirement coverage of {coverage:.0%} (minimum {minimum:.0%}); "
            f"{len(missing)} need(s) without a requirement."
        ),
        suggestion="Go back to the needs that are not covered before closing the specification.",
    )


@document_check(
    id="traceability.version_chain",
    dimension=Dimension.TRACEABILITY,
    severity=Severity.MAJOR,
    description="When a requirement says it supersedes another, the predecessor can be found, in the run or in the history of the project.",
    requires=(
        "Looks at the 'supersedes' field of the envelope. It depends on the TraceabilityProvider "
        "port: with the neutral one it reports that it could not check, instead of failing."
    ),
)
def version_chain(ctx: CheckContext) -> Iterable[Finding]:
    unresolved_without_provider: list[str] = []
    provider_is_null = isinstance(ctx.trace, NullTraceabilityProvider)

    for artifact in ctx.focus_artifacts():
        predecessor = (artifact.supersedes or "").strip()
        if not predecessor:
            continue
        if ctx.envelope.artifact(predecessor) is not None or ctx.trace.knows(predecessor):
            continue
        if provider_is_null:
            unresolved_without_provider.append(f"{artifact.id}->{predecessor}")
            continue
        yield Finding(
            check_id="traceability.version_chain",
            dimension=Dimension.TRACEABILITY,
            severity=Severity.MAJOR,
            level=Level.DOCUMENT,
            target_id=artifact.id,
            excerpt=predecessor,
            message=f"'{artifact.id}' says it supersedes '{predecessor}', which exists neither in the run nor in the history.",
            suggestion="Fix the reference or record the predecessor before versioning.",
        )

    if unresolved_without_provider:
        yield Finding(
            check_id="traceability.version_chain",
            dimension=Dimension.TRACEABILITY,
            severity=Severity.INFO,
            level=Level.DOCUMENT,
            target_id=",".join(unresolved_without_provider[:10]),
            message=(
                f"{len(unresolved_without_provider)} version chain(s) could not be checked: "
                "no traceability provider is plugged in."
            ),
            suggestion="Plug the repository in through the TraceabilityProvider port for this check to count.",
        )


@document_check(
    id="traceability.source_declared",
    dimension=Dimension.TRACEABILITY,
    severity=Severity.MINOR,
    description="The sources of the run say where they came from (meeting, e-mail, conversation).",
    weight=0.5,
    requires="Skipped when the run has no sources.",
    applies=requires_sources,
)
def source_declared(ctx: CheckContext) -> Iterable[Finding]:
    for source in ctx.envelope.sources:
        if not source.origin.strip():
            yield Finding(
                check_id="traceability.source_declared",
                dimension=Dimension.TRACEABILITY,
                severity=Severity.MINOR,
                level=Level.DOCUMENT,
                target_id=source.id,
                message=f"Source '{source.id}' does not say where it came from.",
                suggestion="Record the origin of the material (the meeting of 12 March, the client's e-mail): that is what makes the trace auditable.",
            )
