"""Catalogue of checks.

Importing this package registers every check in the global REGISTRY. A new check
module has to be added to the list below: it is the only place where that happens.
"""

from __future__ import annotations

from . import (  # noqa: F401  (imported for the registration side effect)
    ambiguity,
    completeness,
    conciseness,
    consistency,
    grounding,
    readability,
    redundancy,
    structure,
    traceability,
    verifiability,
)

__all__ = [
    "ambiguity",
    "completeness",
    "conciseness",
    "consistency",
    "grounding",
    "readability",
    "redundancy",
    "structure",
    "traceability",
    "verifiability",
]
