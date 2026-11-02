"""An LLM answer with the wrong shape must not stop a conversation or a meeting.

The first real run fell over a `"requirements": null`. Here each role of the simulator answers
as usual, except for one field, which comes null, empty or of the wrong type. Nothing here calls
an LLM: the point is that no combination reaches the flow as an exception.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest

from gigabrain import meeting
from gigabrain.agents import ValidationSettings
from gigabrain.answers import shape
from gigabrain.council import Council, open_context
from gigabrain.providers import SimulatedProvider

MISSING = object()  # the field does not come at all
# The whole matrix takes minutes; the default covers each kind of wrong value once.
BAD_VALUES = (None, 7, ["x"], MISSING)
if os.environ.get("GIGABRAIN_FULL_MATRIX"):
    BAD_VALUES += ("", "garbage", 0, True, [], [None], [{}], {}, {"x": 1}, [[1]])
REQUEST = ["I want to notify the customer when the payment is confirmed",
           "The customer gets an email within 1 minute after the payment is confirmed", "approved"]
TURNS = [
    ("Analyst", "Okay, next topic: login. How should that work?"),
    ("Client", "Users must log in with their email and a password of at least 8 characters."),
    ("EndUser", "The search page should show results in under 2 seconds for every query."),
    ("Client", "Users must log in with their email and a password of at least 8 characters, again."),
    ("TechLead", "The database must be backed up every 24 hours to an external server."),
]
PROJECT = {
    "id": "p", "title": "Project", "language": "en", "folder": "p",
    "turns": [{"id": f"T{i}", "speaker": s, "text": t, "context": False} for i, (s, t) in enumerate(TURNS, start=1)],
    "gold": [{"id": "g1", "text": TURNS[1][1], "class": "NFR", "subtype": "SE", "turns": ["T2"]}],
}


def paths_of(answer: dict) -> list[tuple]:
    """Every field of an answer, one level into the lists of dicts: ("requirements", 0, "statement")."""
    found: list[tuple] = []
    for key, value in answer.items():
        found.append((key,))
        items = value if isinstance(value, list) else [value]
        for i, item in enumerate(items):
            if isinstance(item, dict):
                for inner in item:
                    found.append((key, i, inner) if isinstance(value, list) else (key, inner))
    return found


def put(answer: dict, path: tuple, value) -> dict:
    changed = copy.deepcopy(answer)
    target = changed
    for step in path[:-1]:
        target = target[step]
    if value is MISSING:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return changed


VALIDATIONS = (ValidationSettings(judge=True),)
if os.environ.get("GIGABRAIN_FULL_MATRIX"):
    VALIDATIONS += (False,)


class OneBadField(SimulatedProvider):
    """Answers like the simulator, except for `path` of the answers of `role`."""

    def __init__(self, role: str | None = None, path: tuple = (), value=None):
        super().__init__()
        self.role, self.path, self.value = role, path, value
        self.seen: dict[str, set] = {}

    def complete_json(self, role, system, messages, temperature=None):
        answer = super().complete_json(role, system, messages, temperature)
        self.seen.setdefault(role, set()).update(paths_of(answer))
        if role == self.role:
            try:
                return put(answer, self.path, self.value)
            except (KeyError, IndexError, TypeError):
                return answer
        return answer


class WrongShape(unittest.TestCase):
    maxDiff = None

    def test_a_right_answer_comes_out_as_it_came_in(self):
        provider = OneBadField()
        seen = {}

        def keep(role, system, messages, temperature=None, _call=provider.complete_json):
            answer = _call(role, system, messages, temperature)
            seen.setdefault(role, []).append(answer)
            return answer

        provider.complete_json = keep
        self.conversation(provider, False)
        self.meeting(provider, False)
        self.assertGreaterEqual(len(seen), 8)
        for role, answers in seen.items():
            for answer in answers:
                self.assertEqual(json.dumps(shape(role, answer)), json.dumps(answer), role)

    def test_what_is_not_an_object_is_an_empty_answer(self):
        for answer in (None, [], ["x"], "text", 7):
            self.assertEqual(shape("twin", answer), {})

    def test_a_link_without_a_target_is_removed_and_the_rest_stays(self):
        answer = shape("twin_decide", {"save": True, "statement": "s", "motive": None, "links": [
            {"kind": "refines", "target": "R1", "reason": None}, {"kind": "refines"}, {"kind": 3, "target": "R2"}, "R3", None]})
        self.assertEqual(answer, {"save": True, "statement": "s", "links": [{"kind": "refines", "target": "R1"}]})

    def test_a_requirement_without_a_statement_is_removed(self):
        answer = shape("twin_draft", {"requirements": [{"title": "t"}, {"statement": ""}, {"statement": 7}, {"statement": "ok", "story": 3, "subtype": 9}]})
        self.assertEqual(answer, {"requirements": [{"statement": "ok", "story": None, "subtype": None}]})

    def conversation(self, provider, validation) -> None:
        with tempfile.TemporaryDirectory() as folder:
            ctx = open_context(folder, provider, validation=validation)
            try:
                council = Council(ctx)
                council.start()
                for text in REQUEST:
                    council.talk(text)
            finally:
                ctx.db.close()

    def meeting(self, provider, validation, mode="council") -> dict:
        with tempfile.TemporaryDirectory() as folder:
            ctx = open_context(folder, provider, validation=validation)
            try:
                return meeting.process_and_evaluate(ctx, PROJECT, folder, mode)
            finally:
                ctx.db.close()

    def all_answers(self) -> dict[str, set]:
        """Which fields each role of the simulator answers, seen by running everything once."""
        provider = OneBadField()
        for validation in (False, ValidationSettings(judge=True)):
            self.conversation(provider, validation)
            for mode in meeting.MODES:
                self.meeting(provider, validation, mode)
        # What only a real LLM sends, or what the simulator leaves out.
        provider.seen.setdefault("twin", set()).update({("query", "topics"), ("query", "question"), ("requirements", 0, "links"),
                                                        ("requirements", 0, "base"), ("requirements", 0, "topics"), ("action",),
                                                        ("requirements", 0, "story", "role"), ("requirements", 0, "acceptance_criteria", 0, "then")})
        provider.seen.setdefault("expert_review", set()).update({("statement",), ("class",), ("subtype",), ("duplicate_of",), ("links",)})
        provider.seen.setdefault("twin_decide", set()).update({("links",), ("class",), ("subtype",), ("statement",), ("motive",), ("save",)})
        provider.seen.setdefault("critic_judge", set()).update({("claims",), ("claims", 0, "claim"), ("claims", 0, "verdict")})
        return provider.seen

    def run_all(self, flow) -> list[str]:
        failures = []
        for role, paths in sorted(self.all_answers().items()):
            for path in sorted(paths, key=str):
                for value in BAD_VALUES:
                    for validation in VALIDATIONS:
                        try:
                            flow(OneBadField(role, path, value), validation)
                        except Exception as exc:  # collected so that one run shows every case
                            failures.append(f"{role} {path} = {value!r} (validation {bool(validation)}): {type(exc).__name__}: {exc}")
        return failures

    def test_conversation_with_the_po(self):
        failures = self.run_all(self.conversation)
        self.assertEqual(failures[:40], [], f"{len(failures)} failures")

    def test_meeting_in_every_mode(self):
        failures = []
        for mode in meeting.MODES:
            failures += self.run_all(lambda provider, validation, m=mode: self.meeting(provider, validation, m))
        self.assertEqual(failures[:40], [], f"{len(failures)} failures")


if __name__ == "__main__":
    unittest.main()
