"""Tests of the input contract.

Malformed input has to fail early, with a message that says where the problem
is: this module is the boundary of the layer, and this is where a mistake in a
file (or of an agent) has to be caught.
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from gigabrain.validation.envelope import EnvelopeError, dumps, from_dict, from_json, load
from gigabrain.validation.model import ArtifactKind, RequirementType

EXAMPLES = Path(__file__).resolve().parents[2] / "gigabrain" / "validation" / "examples"

MINIMAL = {
    "schema_version": "1.1",
    "artifacts": [{"id": "R1", "title": "T", "statement": "The system shall do X."}],
}
LEGACY = {
    "schema_version": "1.0",
    "artifacts": [{"id": "REQ-001", "title": "T", "story": "Como X, quero Y, para que Z.", "type": "NFR", "nfr_subtype": "performance"}],
}


def minimal(**artifact_fields) -> dict:
    data = copy.deepcopy(MINIMAL)
    data["artifacts"][0].update(artifact_fields)
    return data


class TestReading(unittest.TestCase):
    def test_minimal_envelope(self):
        art = from_dict(MINIMAL).artifacts[0]
        self.assertEqual(art.kind, ArtifactKind.REQUIREMENT)  # a statement and no kind
        self.assertEqual(art.req_type, RequirementType.UNKNOWN)

    def test_a_1_0_file_is_still_read(self):
        art = from_dict(LEGACY).artifacts[0]
        self.assertEqual(art.kind, ArtifactKind.USER_STORY)
        self.assertEqual((art.req_type, art.nfr_subtype), (RequirementType.NON_FUNCTIONAL, "performance"))
        self.assertIsNone(art.story_parts)

    def test_class_accepts_variants(self):
        for label, expected in (("FR", RequirementType.FUNCTIONAL), ("fr", RequirementType.FUNCTIONAL),
                                ("NFR", RequirementType.NON_FUNCTIONAL), ("non_functional", RequirementType.NON_FUNCTIONAL)):
            self.assertEqual(from_dict(minimal(**{"class": label})).artifacts[0].req_type, expected)

    def test_subtype_accepts_the_codes_the_agents_use(self):
        for code, name in (("PE", "performance"), ("SE", "security"), ("SA", "safety"), ("OT", "other"), ("usability", "usability")):
            art = from_dict(minimal(**{"class": "NFR", "subtype": code})).artifacts[0]
            self.assertEqual(art.nfr_subtype, name)

    def test_kind_accepts_the_values_the_agents_use(self):
        self.assertEqual(from_dict(minimal(kind="business")).artifacts[0].kind, ArtifactKind.REQUIREMENT)
        self.assertEqual(from_dict(minimal(kind="enabler")).artifacts[0].kind, ArtifactKind.TECHNICAL_ENABLER)

    def test_story_in_three_parts(self):
        art = from_dict(minimal(story={"role": "moderator", "goal": "to group reports", "benefit": "I decide once"})).artifacts[0]
        self.assertEqual(art.story_parts.role, "moderator")
        self.assertEqual(art.story, "As a moderator, I want to group reports, so that I decide once.")

    def test_gherkin_criteria(self):
        art = from_dict(minimal(acceptance_criteria=[{"scenario": "S", "given": "a", "when": "b", "then": "c"}])).artifacts[0]
        self.assertEqual(art.criteria[0].then, "c")
        self.assertEqual(art.acceptance_criteria, ["Given a, when b, then c"])

    def test_plain_text_criteria(self):
        art = from_dict(minimal(acceptance_criteria=["When b, the system does c."])).artifacts[0]
        self.assertEqual(art.criteria, [])
        self.assertEqual(art.acceptance_criteria, ["When b, the system does c."])

    def test_links_and_base(self):
        art = from_dict(minimal(base="R3", links=[{"kind": "depends_on", "target": "R2", "reason": "needs it"}])).artifacts[0]
        self.assertEqual([(l.kind, l.target) for l in art.links], [("base", "R3"), ("depends_on", "R2")])
        self.assertEqual(art.linked_ids(), {"R2", "R3"})

    def test_evidence_is_read(self):
        data = minimal(evidence=[{"source_id": "T2"}, {"source_id": "NEED-001", "quote": "text", "reason": "because"}])
        data["sources"] = [{"id": "T2", "text": "what was said", "kind": "turn"}]
        env = from_dict(data)
        self.assertEqual([e.source_id for e in env.artifacts[0].evidence], ["T2", "NEED-001"])
        self.assertEqual(env.source("T2").kind, "turn")

    def test_document_is_read(self):
        data = minimal()
        data["document"] = {"title": "SRS", "sections": [{"id": "introduction", "text": "hi"}]}
        self.assertIsNotNone(from_dict(data).document.section("introduction"))


class TestErrors(unittest.TestCase):
    def test_invalid_json(self):
        with self.assertRaises(EnvelopeError) as ctx:
            from_json("{not json}")
        self.assertIn("Invalid JSON", str(ctx.exception))

    def test_unknown_schema_version(self):
        data = minimal()
        data["schema_version"] = "9.9"
        with self.assertRaises(EnvelopeError) as ctx:
            from_dict(data)
        self.assertIn("9.9", str(ctx.exception))

    def test_artefact_without_an_id(self):
        with self.assertRaises(EnvelopeError) as ctx:
            from_dict({"artifacts": [{"title": "no id"}]})
        self.assertIn("artifacts[0]", str(ctx.exception))

    def test_unknown_artefact_kind(self):
        with self.assertRaises(EnvelopeError) as ctx:
            from_dict(minimal(kind="poem"))
        self.assertIn("poem", str(ctx.exception))

    def test_unknown_class(self):
        with self.assertRaises(EnvelopeError):
            from_dict(minimal(**{"class": "half-functional"}))

    def test_criteria_must_be_a_list(self):
        with self.assertRaises(EnvelopeError):
            from_dict(minimal(acceptance_criteria="a single criterion, as a string"))

    def test_evidence_without_a_source_id(self):
        with self.assertRaises(EnvelopeError) as ctx:
            from_dict(minimal(evidence=[{"quote": "something"}]))
        self.assertIn("source_id", str(ctx.exception))

    def test_envelope_that_is_not_an_object(self):
        with self.assertRaises(EnvelopeError):
            from_json("[1, 2, 3]")


class TestRoundTrip(unittest.TestCase):
    def test_writing_and_reading_back_keeps_the_content(self):
        for name in ("good.json", "bad.json"):
            with self.subTest(file=name):
                original = load(EXAMPLES / name)
                self.assertEqual(original.content_hash(), from_json(dumps(original)).content_hash())

    def test_the_legacy_format_survives_the_round_trip(self):
        original = from_dict(LEGACY)
        self.assertEqual(original.content_hash(), from_json(dumps(original)).content_hash())

    def test_missing_file(self):
        with self.assertRaises(EnvelopeError):
            load(EXAMPLES / "does-not-exist.json")


if __name__ == "__main__":
    unittest.main()
