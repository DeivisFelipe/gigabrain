"""Adapters: translators between the outside world and the validation model.

- `jsonl_store`: reports persisted in a file (ReportStore port), for runs over
  envelope files.

The adapters to GigaBrain itself (requirements as the agents emit them, the
Requirements Repository, the event log) are in validation/host.py.
"""

from __future__ import annotations

from . import jsonl_store

__all__ = ["jsonl_store"]
