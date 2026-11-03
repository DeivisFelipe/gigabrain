"""What each role may answer, and what happens when an LLM answers otherwise.

The prompts ask for one JSON structure, but a model can send a field as null, as the wrong type
or not at all: the first real run fell over `"requirements": null`. Every answer passes here
before an agent reads it (agents/base.py), so the agents only see fields of the right type:

- a field of the wrong type is removed, and the agent uses its usual default
- a field the prompt allows to be null becomes null
- an item of a list that does not make sense without some field is removed

An answer that is already right comes out exactly as it came in. The Critic's judge is not here:
for it, an answer that does not answer is an error it reports (validation/judge.py).
"""

from __future__ import annotations

_REMOVE = object()


class _Nullable:
    def __init__(self, spec):
        self.spec = spec


class _Needed:
    """The dict it belongs to makes no sense without this field, filled in."""

    def __init__(self, spec):
        self.spec = spec


_LINK = {"kind": _Needed(str), "target": _Needed(str), "reason": str}
_REQUIREMENT = {
    "title": str,
    "statement": _Needed(str),
    "ears_pattern": str,
    "story": _Nullable({"role": str, "goal": str, "benefit": str}),
    "acceptance_criteria": [{"scenario": str, "given": str, "when": str, "then": str}],
    "class": str,
    "subtype": _Nullable(str),
    "kind": str,
    "topic": str,
    "topics": [str],
    "base": _Nullable(str),
    "links": [_LINK],
    "reason": str,
}

SHAPES = {
    "twin": {"action": str, "message_to_po": str, "query": _Nullable({"question": str, "topics": [str]}), "requirements": [_REQUIREMENT]},
    "twin_draft": {"requirements": [_REQUIREMENT]},
    "twin_revise": {"requirement": _REQUIREMENT},
    "twin_decide": {"save": bool, "statement": str, "class": str, "subtype": _Nullable(str), "links": [_LINK], "motive": str},
    "plain_llm": {"requirements": [{"text": _Needed(str), "class": str, "subtype": _Nullable(str), "turns": [str]}]},
    "leader_decompose": {"subqueries": [{"expert_id": _Nullable(str), "topic": _Needed(str), "description": str, "question": _Needed(str)}]},
    "leader_consolidate": {"summary": str, "conflicts": [str], "questions_for_po": [str], "related_requirements": [str]},
    "expert_answer": {"answer": str, "related_requirements": [str], "conflicts": [str], "questions_for_po": [str], "confidence": str},
    "expert_create": {"knowledge_markdown": str},
    "expert_update": {"knowledge_markdown": str, "changes": [str]},
    "expert_review": {"action": str, "statement": _Nullable(str), "class": _Nullable(str), "subtype": _Nullable(str),
                      "duplicate_of": _Nullable(str), "links": [_LINK], "motive": str},
    "expert_quality": {"class": str, "subtype": _Nullable(str), "motive": str},
}


def _shaped(value, spec):
    if isinstance(spec, _Nullable):
        if value is None:
            return None
        inner = _shaped(value, spec.spec)
        return None if inner is _REMOVE else inner
    if isinstance(spec, list):
        if not isinstance(value, list):
            return _REMOVE
        items = (_shaped(item, spec[0]) for item in value)
        return [item for item in items if item is not _REMOVE]
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            return _REMOVE
        result = dict(value)
        for key, inner in spec.items():
            needed = isinstance(inner, _Needed)
            shaped = _shaped(result[key], inner.spec if needed else inner) if key in result else _REMOVE
            if needed and (shaped is _REMOVE or not shaped):
                return _REMOVE
            if shaped is _REMOVE:
                result.pop(key, None)
            else:
                result[key] = shaped
        return result
    return value if isinstance(value, spec) else _REMOVE


def shape(role: str, answer) -> dict:
    """The answer of a role with only fields of the right type. Not even an object: an empty answer."""
    if not isinstance(answer, dict):
        return {}
    spec = SHAPES.get(role)
    if spec is None:
        return answer
    # The top level has no needed fields: an answer without them is an empty answer of that role.
    return _shaped(answer, spec)
