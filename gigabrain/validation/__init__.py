"""Validation layer of GigaBrain: the deterministic core of the Critic agent.

What this layer does: it receives the intermediate specification the agents
produce (the artefact of each one) and the requirements taken together, and
answers three questions, in this order:

1. is the artefact well formed and verifiable?
2. is what it states grounded in a real source?
3. does the whole hold together: no contradiction, no repetition, no gap?

What this layer does **not** do: generate requirements (the Twin and the experts
do), keep the links between requirements (the repository does) or decide the
final requirement (the PO does).

Every artefact gets one score per competency: five at the item level, five at
the document level. It passes only if every competency passes; what does not
pass goes back to the agent that wrote it (see agents/critic.py).

Minimal use, over an envelope file:

    from gigabrain.validation import load_envelope, validate, to_markdown

    report = validate(load_envelope("gigabrain/validation/examples/good.json"))
    print(to_markdown(report))

Inside GigaBrain the entry points are the Critic agent (agents/critic.py) and
the adapters in validation/host.py.
"""

from __future__ import annotations

from .engine import ValidationEngine, validate
from .envelope import EnvelopeError, dumps as dump_envelope, from_dict, from_json
from .envelope import load as load_envelope
from .model import (
    Artifact,
    ArtifactKind,
    Criterion,
    Dimension,
    DimensionScore,
    Envelope,
    EvidenceRef,
    Finding,
    GlossaryEntry,
    ItemResult,
    Level,
    LinkRef,
    Provenance,
    RequirementType,
    Severity,
    SourceDocument,
    SpecificationDocument,
    Story,
    ValidationReport,
    Verdict,
)
from .policy import DEFAULT_POLICY, LENIENT_POLICY, POLICIES, STRICT_POLICY, Policy
from .registry import REGISTRY, CheckConfig, CheckContext
from .report import to_agent_feedback, to_console, to_json, to_markdown, to_summary
from .scoring import RULESET_VERSION

__all__ = [
    "REGISTRY",
    "RULESET_VERSION",
    "Artifact",
    "ArtifactKind",
    "CheckConfig",
    "CheckContext",
    "Criterion",
    "DEFAULT_POLICY",
    "Dimension",
    "DimensionScore",
    "Envelope",
    "EnvelopeError",
    "EvidenceRef",
    "Finding",
    "GlossaryEntry",
    "ItemResult",
    "LENIENT_POLICY",
    "Level",
    "LinkRef",
    "POLICIES",
    "Policy",
    "Provenance",
    "RequirementType",
    "STRICT_POLICY",
    "Severity",
    "SourceDocument",
    "SpecificationDocument",
    "Story",
    "ValidationEngine",
    "ValidationReport",
    "Verdict",
    "dump_envelope",
    "from_dict",
    "from_json",
    "load_envelope",
    "to_agent_feedback",
    "to_console",
    "to_json",
    "to_markdown",
    "to_summary",
    "validate",
]
