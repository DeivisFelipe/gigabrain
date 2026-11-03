"""The Critic's scores of a saved requirement, for the read routes of the API and the dashboard.

Outside validation/ on purpose: importing anything inside it loads the whole layer. These
routes do not depend on validation: if it is broken, or cannot score this requirement,
"validation" comes back null and the rest of the page is served as always.
"""

from __future__ import annotations

import sys


def validation_of(db, requirement_id: str) -> dict | None:
    try:
        from .validation.host import validation_of as scores
        return scores(db, requirement_id)
    except Exception as exc:
        print(f"validation of {requirement_id} unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
