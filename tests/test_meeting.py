"""Tests of the dataset reader, meeting processing, evaluation, scenarios, statistics and annotation."""

from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest

from gigabrain import annotation, dataset, evaluation, meeting, scenarios, stats
from gigabrain.council import open_context
from gigabrain.providers import SimulatedProvider

UTTERANCES = [
    ("Analyst", "Okay, next topic: login. How should that work?"),
    ("Client", "Uh, users must log in with their email and a password of at least 8 characters."),
    ("Analyst", "Just to confirm, users must log in with their email and a password?"),
    ("EndUser", "The the search page should show results in under two seconds."),
    ("TechLead", "Sorry, I was on mute."),
]
GOLD = [
    ("p:R1", "Users must log in with their email and a password of at least 8 characters.", "NFR", "SE", "T2"),
    ("p:R2", "The search page shall show results in under two seconds.", "NFR", "PE", "T4"),
    ("p:R3", "The system shall export reports to PDF.", "FR", None, "T4"),
]

SESSION_UTTERANCES = [
    ("Analyst", "Okay, next topic: login. How should that work?"),
    ("Client", "Users must log in with their email and a password of at least 8 characters."),
    ("Analyst", "So, let's talk about search. What do you need there?"),
    ("EndUser", "The search page should show results in under 2 seconds for every query."),
    ("Analyst", "Okay, next topic: reports. How should that work?"),
    ("Client", "The system shall export monthly reports to PDF for the finance team."),
    ("Analyst", "So, let's talk about backup. What do you need there?"),
    ("TechLead", "The database must be backed up every 24 hours to an external server."),
]
SESSION_GOLD = [
    ("p:R1", SESSION_UTTERANCES[1][1], "NFR", "SE", "T2"),
    ("p:R2", SESSION_UTTERANCES[3][1], "NFR", "PE", "T4"),
    ("p:R3", SESSION_UTTERANCES[5][1], "FR", None, "T6"),
    ("p:R4", SESSION_UTTERANCES[7][1], "NFR", "FT", "T8"),
]


def make_dataset(folder: str, utterances=UTTERANCES, gold=GOLD) -> None:
    """Build a minimal project in the same format as EntradasGigabrain (Portuguese file names)."""
    base = os.path.join(folder, "test-project")
    os.makedirs(base)
    text, segments = "", []
    for i, (speaker, utterance) in enumerate(utterances, start=1):
        text += f"{speaker}: "
        start = len(text)
        text += utterance
        seg = {"id": f"T{i}", "start": start, "end": len(text), "speaker": speaker}
        ids = [g[0] for g in gold if g[4] == f"T{i}"]
        if ids:
            seg["gt_ids"] = ids
        segments.append(seg)
        text += "\n"
    with open(os.path.join(base, dataset.TRANSCRIPT_FILE), "w", encoding="utf-8") as f:
        f.write(text)
    gold_file = {
        "doc_id": "test-project", "doc_title": "Test project", "input": {"segments": segments},
        "requirements": [{"id": i, "text": t, "class": c, "subtype": s, "trace": {"input_segments": [turn], "reachable": True}}
                         for i, t, c, s, turn in gold],
    }
    with open(os.path.join(base, dataset.GOLD_FILE), "w", encoding="utf-8") as f:
        json.dump(gold_file, f)
    with open(os.path.join(folder, dataset.INDEX_FILE), "w", encoding="utf-8") as f:
        f.write("id_documento,nome_projeto,requisitos_dialogo,qualidade_tier,palavras_transcricao,arquivo_transcricao\n")
        f.write(f"test-project,Test project,{len(gold)},A,40,test-project/{dataset.TRANSCRIPT_FILE}\n")


class TempFolder(unittest.TestCase):
    def setUp(self):
        # addCleanup runs in reverse order: databases close before the folder is deleted.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def context(self, name):
        ctx = open_context(os.path.join(self.tmp.name, name), SimulatedProvider())
        self.addCleanup(ctx.db.close)
        return ctx


class TestEvaluation(unittest.TestCase):
    def test_metrics(self):
        gold = [
            {"id": "g1", "text": "Users must log in with email and password", "class": "NFR", "subtype": "SE", "turns": ["T2"]},
            {"id": "g2", "text": "Export reports to PDF format", "class": "FR", "subtype": None, "turns": ["T4"]},
        ]
        predicted = [
            {"id": "R1", "text": "users log in with email and password", "class": "FR", "subtype": None, "turns": ["T2"]},
            {"id": "R2", "text": "the dashboard has a dark theme", "class": "NFR", "subtype": "LF", "turns": ["T9"]},
        ]
        turns = [{"id": "T2", "text": "users log in with email and password"}]
        m = evaluation.evaluate(predicted, gold, turns)["metrics"]
        self.assertEqual((m["hits"], m["precision"], m["recall"], m["f1"]), (1, 0.5, 0.5, 0.5))
        self.assertEqual(m["class"], 0.0)       # predicted FR, it was NFR
        self.assertEqual(m["subtype"], 0.0)
        self.assertEqual(m["trace"], 1.0)
        self.assertEqual(m["unsupported"], 0.5)  # R2 cites a turn that does not exist

    def test_one_to_one_matching(self):
        gold = [{"id": "g1", "text": "export reports to pdf", "class": "FR", "subtype": None, "turns": []}]
        predicted = [{"id": f"R{i}", "text": "export reports to pdf", "class": "FR", "subtype": None, "turns": []} for i in (1, 2)]
        m = evaluation.evaluate(predicted, gold, [])["metrics"]
        self.assertEqual((m["hits"], m["precision"], m["recall"]), (1, 0.5, 1.0))


class TestMeeting(TempFolder):
    def setUp(self):
        super().setUp()
        make_dataset(self.tmp.name)
        self.project = dataset.load_project("test-project", self.tmp.name)

    def test_load_project(self):
        self.assertEqual(dataset.list_projects(self.tmp.name)[0]["folder"], "test-project")
        self.assertEqual(self.project["turns"][1]["text"], UTTERANCES[1][1])
        self.assertEqual([g["turns"] for g in self.project["gold"]], [["T2"], ["T4"], ["T4"]])

    def test_three_modes(self):
        for mode in meeting.MODES:
            ctx = self.context(mode)
            r = meeting.process_and_evaluate(ctx, self.project, os.path.join(self.tmp.name, mode), mode)
            # The Analyst (confirmation) and the off-topic utterance do not become requirements.
            self.assertEqual((r["metrics"]["predicted"], r["metrics"]["hits"]), (2, 2), mode)
            self.assertEqual(r["not_found"], ["p:R3"])
            self.assertTrue(os.path.exists(os.path.join(self.tmp.name, mode, "evaluation.json")))
            req = ctx.db.get_requirement("R1")
            experts = {e["id"] for e in ctx.db.list_experts()}
            if mode == "council":
                self.assertIn({"kind": "turn", "ref": "T2", "project": "test-project"}, req["sources"])
                self.assertIn("quality", experts)  # fixed expert for class/subtype
                self.assertEqual(r["metrics"]["ears"], 1.0)
                self.assertEqual(r["metrics"]["gherkin"], 1.0)
            else:
                self.assertEqual(experts, set(), mode)

    def test_duplicate_is_discarded_by_the_twin(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repeated = "Users must log in with their email and a password of at least 8 characters."
        make_dataset(tmp.name, UTTERANCES[:2] + [("EndUser", repeated)])
        project = dataset.load_project("test-project", tmp.name)

        council_ctx, single_ctx = self.context("council-dup"), self.context("single-dup")
        council = meeting.process(council_ctx, project, "council")
        single = meeting.process(single_ctx, project, "single_agent")

        self.assertEqual((len(council["predicted"]), len(council["discarded"])), (1, 1))
        events = council_ctx.db.list_events(council["conversation_id"])
        self.assertEqual([e["content"]["save"] for e in events if e["kind"] == "decision"], [True, False])
        saved = next(e for e in events if e["kind"] == "requirement_saved")
        self.assertEqual((saved["sender"], saved["recipient"]), ("twin", "repository"))
        self.assertIn("twin", saved["content"]["decided_by"])
        self.assertEqual(len(single["predicted"]), 2)  # without the council the duplicate goes through

    def test_knowledge_is_noted_without_llm(self):
        ctx = self.context("knowledge")
        meeting.process(ctx, self.project, "council")
        roles = [e["content"]["role"] for e in ctx.db.list_events() if e["kind"] == "llm_call"]
        self.assertNotIn("expert_update", roles)  # few requirements per topic: no reorganization yet
        reasons = [v["reason"] for e in ctx.db.list_experts() for v in ctx.db.knowledge_versions(e["id"])]
        self.assertIn("R1 saved", reasons)


class TestScenario(TempFolder):
    def setUp(self):
        super().setUp()
        make_dataset(self.tmp.name, SESSION_UTTERANCES, SESSION_GOLD)
        self.project = dataset.load_project("test-project", self.tmp.name)

    def test_generation_is_deterministic_and_injects_in_later_sessions(self):
        s1 = scenarios.generate_sessions(self.project, n_sessions=2, fraction=0.25)
        s2 = scenarios.generate_sessions(self.project, n_sessions=2, fraction=0.25)
        self.assertEqual(s1, s2)
        self.assertEqual(len(s1["sessions"]), 2)
        self.assertEqual({i["kind"] for i in s1["injections"]}, {"repetition", "change"})
        for inj in s1["injections"]:
            self.assertEqual(inj["session"], 2)
            self.assertTrue(inj["turn"].startswith("T9"))

    def test_council_avoids_repetition_and_links_change(self):
        scenario = scenarios.generate_sessions(self.project, n_sessions=2, fraction=0.25)
        metrics = {}
        for mode in ("council", "single_agent"):
            ctx = self.context("sessions-" + mode)
            r = meeting.process_sessions_and_evaluate(ctx, self.project, scenario, os.path.join(self.tmp.name, "sessions-" + mode), mode)
            metrics[mode] = r["metrics"]
            self.assertEqual(len(r["conversations"]), 2)  # one conversation per meeting, same repository
        self.assertEqual(metrics["council"]["repetitions_avoided"], 1.0)
        self.assertEqual(metrics["council"]["changes_linked"], 1.0)
        self.assertEqual(metrics["single_agent"]["changes_linked"], 0.0)


class TestStats(unittest.TestCase):
    def test_wilcoxon_matches_scipy(self):
        # scipy.stats.wilcoxon(a, b) -> p = 0.0390625 (exact)
        a = [1.83, 0.50, 1.62, 2.48, 1.68, 1.88, 1.55, 3.06, 1.30]
        b = [0.878, 0.647, 0.598, 2.05, 1.06, 1.29, 1.06, 3.14, 1.29]
        r = stats.wilcoxon_paired(a, b)
        self.assertAlmostEqual(r["p"], 0.0391, places=4)
        self.assertEqual((r["w_plus"], r["w_minus"]), (40.0, 5.0))

    def test_summary_uses_the_mean_of_repetitions(self):
        rows = [{"project": p, "mode": m, "rep": rep, "f1": v}
                for p, base in (("a", 0.8), ("b", 0.6), ("c", 0.7))
                for m, extra in (("council", 0.1), ("single_agent", 0.0))
                for rep, v in ((1, base + extra), (2, base + extra + 0.02))]
        summary = stats.summarize(rows)
        self.assertAlmostEqual(summary["per_mode"]["council"]["f1"]["mean"], 0.81, places=4)
        comp = next(c for c in summary["comparisons"] if c["metric"] == "f1")
        self.assertEqual((comp["a"], comp["b"], comp["effect"]), ("council", "single_agent", 1.0))


class TestAnnotation(TempFolder):
    def test_kappa(self):
        path = os.path.join(self.tmp.name, "a.csv")
        a1 = ["c", "c", "c", "i", "c", "i", "c", "c"]
        a2 = ["c", "c", "i", "i", "c", "i", "c", "yes"]  # "yes" also counts as correct
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=annotation.COLUMNS, delimiter=";")
            w.writeheader()
            for i, (x, y) in enumerate(zip(a1, a2)):
                w.writerow({"id": i, "annotator_1": x, "annotator_2": y})
        r = annotation.kappa(path)
        # agreement 7/8; expected = 6/8*5/8 + 2/8*3/8 = 0.5625 -> kappa = (0.875-0.5625)/(1-0.5625)
        self.assertEqual(r["annotated"], 8)
        self.assertAlmostEqual(r["kappa"], round((0.875 - 0.5625) / (1 - 0.5625), 4))


@unittest.skipUnless(os.path.exists(os.path.join(dataset.DEFAULT_FOLDER, dataset.INDEX_FILE)),
                     "EntradasGigabrain dataset not cloned next to the project")
class TestRealDataset(unittest.TestCase):
    def test_every_project_loads(self):
        for p in dataset.list_projects():
            self.assertTrue(dataset.load_project(p["folder"])["turns"], p["folder"])


if __name__ == "__main__":
    unittest.main()
