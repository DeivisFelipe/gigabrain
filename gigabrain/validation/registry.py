"""Check registry.

A check is a small, independent unit: it receives a context and returns findings.
Registering them in a catalogue (instead of calling them one after the other
inside the engine) is a design decision. What it buys:

- someone can add a check without touching the engine;
- the experiment of the paper can switch single checks on and off and measure
  the effect of each one (ablation study);
- the report can say exactly which rules ran and which were skipped, and why.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Protocol

from .model import Artifact, Dimension, Envelope, Finding, Level, Severity
from .ports import NullTraceabilityProvider, TraceabilityProvider


@dataclass
class CheckConfig:
    """Adjustable parameters of the checks.

    They all live together, each with an explicit default, because every
    threshold is a debatable choice that has to be reported in the paper.
    """

    # structure
    #: The Twin's prompt asks for "at least the main scenario and one exception or
    #: limit scenario", hence two; an enabler may have a single one.
    min_acceptance_criteria: int = 2
    min_acceptance_criteria_enabler: int = 1

    # understandability
    max_words_per_sentence: int = 30
    max_words_per_criterion: int = 40

    # correctness / grounding
    #: Minimum share of a literal quote that must be found in the cited source.
    min_quote_containment: float = 0.85
    #: Minimum share of the statement's content words the cited source must also
    #: use, when the agent cites a source without quoting it. Same value as
    #: SUPPORT_THRESHOLD in gigabrain/evaluation.py, so "unsupported" means the
    #: same thing in the evaluation and in the gate.
    min_statement_support: float = 0.30
    #: Minimum similarity for a "this repeats R3" claim to be believed.
    min_duplicate_claim_similarity: float = 0.30
    require_evidence: bool = True

    # non-redundancy
    duplicate_threshold: float = 0.80
    near_duplicate_threshold: float = 0.65

    # conciseness
    max_words_per_artifact: int = 220
    max_filler_ratio: float = 0.04

    # traceability
    min_forward_coverage: float = 1.0
    min_backward_coverage: float = 0.80

    # completeness
    required_sections: tuple[str, ...] = (
        "introduction",
        "stakeholders",
        "use_cases",
        "glossary",
    )

    #: Checks switched off in this run (by id). Used for ablation.
    disabled_checks: frozenset[str] = frozenset()

    #: Severity overridden per check, when the policy wants to adjust one.
    severity_overrides: dict[str, Severity] = field(default_factory=dict)


@dataclass
class CheckContext:
    """Everything a check may look at.

    `trace` is the port to the traceability data. The default knows nothing, and
    the checks have to tell "I do not know" from "it does not exist" (ports.py).

    `focus` narrows a run to some artefacts of the envelope, the others being
    context: one candidate checked against the saved repository, for instance.
    Item checks then run only on the focus, and the pairwise document checks
    only on pairs that involve it. Empty means every artefact.
    """

    envelope: Envelope
    config: CheckConfig
    trace: TraceabilityProvider = field(default_factory=NullTraceabilityProvider)
    focus: frozenset[str] = frozenset()

    def glossary_terms(self) -> list[str]:
        return [g.term for g in self.envelope.glossary]

    def in_focus(self, artifact_id: str) -> bool:
        return not self.focus or artifact_id in self.focus

    def focus_artifacts(self) -> list[Artifact]:
        return [a for a in self.envelope.artifacts if self.in_focus(a.id)]

    def pairs(self, artifacts: list[Artifact] | None = None) -> Iterator[tuple[Artifact, Artifact]]:
        """Pairs of artefacts to compare: all of them, or only those that touch the focus."""
        items = self.envelope.artifacts if artifacts is None else artifacts
        for left, right in itertools.combinations(items, 2):
            if not self.focus or left.id in self.focus or right.id in self.focus:
                yield left, right


class ItemCheckFn(Protocol):
    def __call__(self, artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]: ...


class DocumentCheckFn(Protocol):
    def __call__(self, ctx: CheckContext) -> Iterable[Finding]: ...


@dataclass(frozen=True)
class Check:
    """Metadata plus the function of a check."""

    id: str
    level: Level
    dimension: Dimension
    default_severity: Severity
    description: str
    fn: Callable[..., Iterable[Finding]]
    #: Weight of the rule in the score of its dimension (see scoring.py).
    weight: float = 1.0
    #: When the check applies, in words, for the catalogue and the report.
    requires: str = ""
    #: Verifiable precondition. It receives the context and returns `None` when
    #: the check can run, or the reason why it cannot.
    #:
    #: It exists for a specific reason: without it, a check that depends on
    #: material absent from the run ends up reporting "it does not exist" where
    #: the right answer is "I do not know". That happened when the raw output of
    #: the Twin was validated without its sources: the cited source was reported
    #: as missing and the artefact failed for a gap of the run, not for a defect
    #: of its own.
    applies: Callable[["CheckContext"], str | None] | None = None


class CheckRegistry:
    """Ordered catalogue of checks, indexed by id."""

    def __init__(self) -> None:
        self._checks: dict[str, Check] = {}

    def register(self, check: Check) -> Check:
        if check.id in self._checks:
            raise ValueError(f"duplicate check: {check.id}")
        self._checks[check.id] = check
        return check

    def get(self, check_id: str) -> Check | None:
        return self._checks.get(check_id)

    def all(self) -> list[Check]:
        return sorted(self._checks.values(), key=lambda c: (c.level.value, c.dimension.value, c.id))

    def by_level(self, level: Level) -> list[Check]:
        return [c for c in self.all() if c.level is level]

    def by_dimension(self, dimension: Dimension) -> list[Check]:
        return [c for c in self.all() if c.dimension is dimension]

    def ids(self) -> list[str]:
        return [c.id for c in self.all()]

    def __len__(self) -> int:
        return len(self._checks)


#: Global registry. The modules in validation/checks/ register themselves when
#: imported; validation/checks/__init__.py makes sure all of them are imported.
REGISTRY = CheckRegistry()


def item_check(
    *,
    id: str,
    dimension: Dimension,
    severity: Severity,
    description: str,
    weight: float = 1.0,
    requires: str = "",
    applies: Callable[[CheckContext], str | None] | None = None,
) -> Callable[[ItemCheckFn], ItemCheckFn]:
    """Decorator that registers a check of a single artefact."""

    def wrapper(fn: ItemCheckFn) -> ItemCheckFn:
        REGISTRY.register(
            Check(
                id=id,
                level=Level.ITEM,
                dimension=dimension,
                default_severity=severity,
                description=description,
                fn=fn,
                weight=weight,
                requires=requires,
                applies=applies,
            )
        )
        return fn

    return wrapper


def document_check(
    *,
    id: str,
    dimension: Dimension,
    severity: Severity,
    description: str,
    weight: float = 1.0,
    requires: str = "",
    applies: Callable[[CheckContext], str | None] | None = None,
) -> Callable[[DocumentCheckFn], DocumentCheckFn]:
    """Decorator that registers a check of the consolidated specification."""

    def wrapper(fn: DocumentCheckFn) -> DocumentCheckFn:
        REGISTRY.register(
            Check(
                id=id,
                level=Level.DOCUMENT,
                dimension=dimension,
                default_severity=severity,
                description=description,
                fn=fn,
                weight=weight,
                requires=requires,
                applies=applies,
            )
        )
        return fn

    return wrapper
