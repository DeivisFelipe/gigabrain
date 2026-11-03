"""Tests of the dataset with variants: index, schema 2.0, several meetings and what schema 2.0 measures."""

from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest

from gigabrain import dataset, evaluation_meetings, meeting
from gigabrain.council import open_context
from gigabrain.providers import SimulatedProvider

INDEX_COLUMNS = ["variante", "id_documento", "nome_projeto", "qualidade_tier", "n_reunioes", "palavras_transcricao",
                 "requisitos_dialogo", "requisitos_esperados", "pasta"]

MEETING_1 = [
    ("Analyst", "Okay, next topic: login. How should that work?"),
    ("Client", "Users must log in with their email and a password of at least 8 characters."),
    ("EndUser", "The report page shall export monthly sales to a spreadsheet file for managers."),
]
MEETING_2 = [
    ("Analyst", "Hi again. Next topic: search. What do you need there?"),
    ("EndUser", "The search page should show results in under two seconds for every query."),
    ("TechLead", "The audit screen shall list every failed access from the last few days."),
]
GOLD_V2 = [
    {"id": "p:R1", "text": MEETING_1[1][1], "class": "NFR", "subtype": "SE", "status": "clear", "expected_in_output": True,
     "phenomena": [], "trace": {"input_segments": ["M1:T2"], "reachable": True}},
    {"id": "p:R2", "text": MEETING_2[1][1], "class": "NFR", "subtype": "PE", "status": "clear", "expected_in_output": True,
     "phenomena": [], "trace": {"input_segments": ["M2:T2"], "reachable": True}},
    {"id": "p:R3", "text": "The audit screen shall list every failed access from the last 30 days.", "class": "FR", "subtype": None,
     "status": "ambiguous", "expected_in_output": True,
     "phenomena": [{"type": "ambiguity", "details": {"removed": "30 days"}}],
     "trace": {"input_segments": ["M2:T3"], "reachable": True}},
    {"id": "p:R4", "text": MEETING_1[2][1], "class": "FR", "subtype": None, "status": "rejected", "expected_in_output": False,
     "phenomena": [], "trace": {"input_segments": ["M1:T3"], "reachable": True}},
    {"id": "p:R5", "text": "The system shall print invoices on thermal paper for every store.", "class": "FR", "subtype": None,
     "status": "omitted", "expected_in_output": False, "phenomena": [], "trace": {"input_segments": [], "reachable": False}},
]
GT_IDS = {"M1:T2": ["p:R1"], "M2:T2": ["p:R2"], "M2:T3": ["p:R3"], "M1:T3": ["p:R4"]}


def _write_meeting(path: str, meeting_id: str, utterances) -> list[dict]:
    text, segments = f"Meeting {meeting_id}\n\n", []
    for i, (speaker, utterance) in enumerate(utterances, start=1):
        text += f"{speaker}: "
        start = len(text)
        text += utterance
        seg_id = f"{meeting_id}:T{i}"
        segments.append({"id": seg_id, "start": start, "end": len(text), "speaker": speaker, "act": "state",
                         "gt_ids": GT_IDS.get(seg_id, [])})
        text += "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return segments


def make_variants_dataset(folder: str) -> None:
    """The layout of EntradasGigabrain: v1-original, variante_A/rules-demo, one index for both."""
    v1 = os.path.join(folder, "v1-original", "test-project")
    os.makedirs(v1)
    with open(os.path.join(v1, dataset.TRANSCRIPT_FILE), "w", encoding="utf-8") as f:
        f.write("Client: The system shall export reports to PDF.\n")
    with open(os.path.join(v1, dataset.GOLD_FILE), "w", encoding="utf-8") as f:
        json.dump({"schema_version": "1.0", "doc_id": "p", "doc_title": "Project", "input": {"segments": [
            {"id": "T1", "start": 8, "end": 47, "speaker": "Client", "gt_ids": ["p:R1"]}]},
            "requirements": [{"id": "p:R1", "text": "The system shall export reports to PDF.", "class": "FR", "subtype": None,
                              "trace": {"input_segments": ["T1"], "reachable": True}}]}, f)

    v2 = os.path.join(folder, "variante_A", "rules-demo", "test-project")
    os.makedirs(v2)
    meetings = []
    for meeting_id, name, utterances in (("M1", "reuniao_01.txt", MEETING_1), ("M2", "reuniao_02.txt", MEETING_2)):
        meetings.append({"id": meeting_id, "file": name, "kind": "kickoff", "segments": _write_meeting(os.path.join(v2, name), meeting_id, utterances)})
    with open(os.path.join(v2, dataset.GOLD_FILE), "w", encoding="utf-8") as f:
        json.dump({"schema_version": "2.0", "doc_id": "p", "doc_title": "Project", "variant": "rules-demo",
                   "input": {"n_meetings": 2, "meetings": meetings}, "requirements": GOLD_V2,
                   "distractors": [{"id": "p:D001", "text": "The search page should show results in under ten seconds for every query.",
                                    "requirement": "p:R2", "segments": []}],
                   "issues": [{"id": "p:I001", "type": "ambiguity", "requirement": "p:R3"}]}, f)

    with open(os.path.join(folder, dataset.INDEX_FILE), "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=INDEX_COLUMNS)
        writer.writeheader()
        writer.writerow({"variante": "v1-original", "id_documento": "test-project", "nome_projeto": "Project", "qualidade_tier": "A",
                         "n_reunioes": 1, "palavras_transcricao": 8, "requisitos_dialogo": 1, "requisitos_esperados": 1,
                         "pasta": "v1-original/test-project"})
        writer.writerow({"variante": "rules-demo", "id_documento": "test-project", "nome_projeto": "Project", "qualidade_tier": "A",
                         "n_reunioes": 2, "palavras_transcricao": 60, "requisitos_dialogo": 5, "requisitos_esperados": 3,
                         "pasta": "variante_A/rules-demo/test-project"})


class TestVariantsDataset(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        make_variants_dataset(self.tmp.name)

    def test_the_reference_is_the_default(self):
        self.assertEqual(dataset.list_variants(self.tmp.name), ["v1-original", "rules-demo"])
        listed = dataset.list_projects(self.tmp.name)
        self.assertEqual([(p["folder"], p["requirements"], p["available"]) for p in listed], [("test-project", 1, True)])
        project = dataset.load_project("test-project", self.tmp.name)
        self.assertEqual([t["id"] for t in project["turns"]], ["T1"])
        self.assertNotIn("meetings", project)
        self.assertNotIn("negatives", project)

    def test_unknown_variant_says_which_exist(self):
        with self.assertRaisesRegex(dataset.DatasetError, "rules-demo"):
            dataset.list_projects(self.tmp.name, "nope")
        with self.assertRaisesRegex(dataset.DatasetError, "rules-demo"):
            dataset.load_project("test-project", self.tmp.name, "nope")

    def test_schema_2_keeps_the_meetings_and_splits_what_should_come_out(self):
        project = dataset.load_project("test-project", self.tmp.name, "rules-demo")
        self.assertEqual([m["id"] for m in project["meetings"]], ["M1", "M2"])
        self.assertEqual([t["id"] for t in project["turns"]], ["M1:T1", "M1:T2", "M1:T3", "M2:T1", "M2:T2", "M2:T3"])
        self.assertEqual(project["turns"][1]["text"], MEETING_1[1][1])
        self.assertEqual([g["id"] for g in project["gold"]], ["p:R1", "p:R2", "p:R3"])
        self.assertEqual(project["gold"][0]["turns"], ["M1:T2"])
        self.assertEqual(sorted((n["id"], n["kind"]) for n in project["negatives"]),
                         [("p:D001", "distractor"), ("p:R4", "rejected"), ("p:R5", "omitted")])

    def test_a_dataset_without_variants_is_the_reference_only(self):
        with tempfile.TemporaryDirectory() as flat:
            os.makedirs(os.path.join(flat, "a"))
            with open(os.path.join(flat, dataset.INDEX_FILE), "w", encoding="utf-8") as f:
                f.write("nome_projeto,requisitos_dialogo,qualidade_tier,palavras_transcricao,arquivo_transcricao\n")
                f.write(f"A,1,A,10,a/{dataset.TRANSCRIPT_FILE}\n")
            self.assertEqual(dataset.list_variants(flat), ["v1-original"])
            self.assertEqual(dataset.list_projects(flat)[0]["folder"], "a")
            with self.assertRaises(dataset.DatasetError):
                dataset.list_projects(flat, "rules-clean")


class TestSeveralMeetings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        make_variants_dataset(self.tmp.name)
        self.project = dataset.load_project("test-project", self.tmp.name, "rules-demo")

    def run_mode(self, mode: str, validation=False) -> tuple[dict, list[dict], str]:
        folder = os.path.join(self.tmp.name, "run", mode, str(bool(validation)))
        ctx = open_context(folder, SimulatedProvider(), validation=validation)
        report = meeting.process_and_evaluate(ctx, self.project, folder, mode)
        conversations = ctx.db.list_conversations()
        ctx.db.close()
        return report, conversations, folder

    def test_each_meeting_is_one_conversation_over_the_same_repository(self):
        report, conversations, _ = self.run_mode("council")
        self.assertEqual(len(conversations), 2)
        self.assertEqual(len(report["conversations"]), 2)
        cited = {t for p in report["predicted"] for t in p["turns"]}
        self.assertTrue(any(t.startswith("M1:") for t in cited))
        self.assertTrue(any(t.startswith("M2:") for t in cited))
        self.assertTrue(cited <= {t["id"] for t in self.project["turns"]})

    def test_plain_llm_reads_everything_at_once(self):
        report, conversations, _ = self.run_mode("plain_llm")
        self.assertEqual(len(conversations), 1)
        self.assertNotIn("conversations", report)
        self.assertTrue({"M1:T2", "M2:T2"} <= {t for p in report["predicted"] for t in p["turns"]})

    def test_the_critic_finds_the_turns_of_every_meeting(self):
        report, _, _ = self.run_mode("council", validation=True)
        self.assertIsNotNone(report["validation"])
        self.assertEqual(report["validation"]["requirements"], len(report["predicted"]))
        self.assertIn("valid", report["metrics"])

    def test_what_schema_2_measures_is_in_the_report(self):
        report, _, folder = self.run_mode("council")
        self.assertEqual(report["variant"], "rules-demo")
        for metric in ("distractor_hits", "rejected_hits", "omitted_hits", "detail_hallucination"):
            self.assertIn(metric, report["metrics"])
        self.assertEqual(report["metrics"]["rejected_hits"], 1)  # the simulator saves the withdrawn requirement
        self.assertEqual(report["recall_by_status"]["clear"], 1.0)
        with open(os.path.join(folder, "evaluation.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["metrics"]["rejected_hits"], 1)


class TestWhatSchema2Measures(unittest.TestCase):
    PROJECT = {
        "turns": [{"id": "M1:T1", "speaker": "Client", "text": "The audit screen shall list every failed access from the last few days."}],
        "gold": [{"id": "g1", "text": "The audit screen shall list every failed access from the last 30 days.", "status": "ambiguous",
                  "phenomena": [{"type": "ambiguity", "details": {"removed": "30 days"}}]},
                 {"id": "g2", "text": "Users must log in with their email.", "status": "clear", "phenomena": []}],
        "negatives": [{"id": "d1", "text": "The search page should show results in under ten seconds.", "kind": "distractor"},
                      {"id": "r1", "text": "The report page shall export monthly sales.", "kind": "rejected"},
                      {"id": "o1", "text": "The system shall print invoices on thermal paper.", "kind": "omitted"}],
    }

    def report(self, predicted, pairs, false_positives):
        return evaluation_meetings.evaluate({"predicted": predicted, "pairs": pairs, "false_positives": false_positives}, self.PROJECT)

    def test_a_prediction_with_the_removed_number_is_a_hallucination(self):
        predicted = [{"id": "R1", "text": "The audit screen shall list every failed access from the last 30 days."}]
        extra = self.report(predicted, [{"predicted": "R1", "gold": "g1"}], [])
        self.assertEqual(extra["metrics_meetings"]["detail_hallucination"], 1.0)
        self.assertEqual(extra["detail_hallucinated"], ["R1"])

    def test_keeping_what_was_said_is_not_a_hallucination(self):
        predicted = [{"id": "R1", "text": "The audit screen shall list every failed access from the last few days."}]
        extra = self.report(predicted, [{"predicted": "R1", "gold": "g1"}], [])
        self.assertEqual(extra["metrics_meetings"]["detail_hallucination"], 0.0)

    def test_without_a_removed_detail_there_is_nothing_to_measure(self):
        extra = self.report([{"id": "R1", "text": "Users must log in with their email."}], [{"predicted": "R1", "gold": "g2"}], [])
        self.assertIsNone(extra["metrics_meetings"]["detail_hallucination"])
        self.assertEqual(extra["recall_by_status"], {"ambiguous": 0.0, "clear": 1.0})

    def test_each_negative_counts_once_and_a_paired_prediction_is_not_a_hit(self):
        predicted = [
            {"id": "R1", "text": "The search page should show results in under ten seconds."},
            {"id": "R2", "text": "The search page should show results in under ten seconds."},
            {"id": "R3", "text": "The system shall print invoices on thermal paper."},
            {"id": "R4", "text": "Something else about the weather forecast widget."},
        ]
        extra = self.report(predicted, [], ["R1", "R2", "R3", "R4"])
        self.assertEqual(extra["metrics_meetings"]["distractor_hits"], 1)
        self.assertEqual(extra["metrics_meetings"]["omitted_hits"], 1)
        self.assertEqual(extra["metrics_meetings"]["rejected_hits"], 0)


@unittest.skipUnless(os.path.exists(os.path.join(dataset.DEFAULT_FOLDER, dataset.INDEX_FILE)),
                     "EntradasGigabrain dataset not cloned next to the project")
class TestRealVariants(unittest.TestCase):
    def test_every_project_of_every_variant_loads(self):
        for variant in dataset.list_variants():
            for p in dataset.list_projects(variant=variant):
                with self.subTest(variant=variant, project=p["folder"]):
                    self.assertTrue(p["available"])
                    project = dataset.load_project(p["folder"], variant=variant)
                    self.assertTrue(project["turns"])
                    ids = [t["id"] for t in project["turns"]]
                    self.assertEqual(len(ids), len(set(ids)))
                    self.assertTrue(all(t["text"] for t in project["turns"]))
                    self.assertEqual(len(project["gold"]), p["requirements"])
                    self.assertTrue({t for g in project["gold"] for t in g["turns"]} <= set(ids))

    def test_the_reference_has_the_same_requirements_as_ever(self):
        self.assertEqual(sum(len(dataset.load_project(p["folder"])["gold"]) for p in dataset.list_projects()), 820)


if __name__ == "__main__":
    unittest.main()
