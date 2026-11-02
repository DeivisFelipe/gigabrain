"""An error in validation/ does not stop a run without validation.

Each test runs the commands in a copy of the code with validation/ broken on purpose, in a
new process, because the point is what happens at import time.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BREAKS = {
    "raises at import": ("gigabrain/validation/scoring.py", '\nraise RuntimeError("broken on purpose")\n'),
    "syntax error": ("gigabrain/validation/__init__.py", "\ndef broken(:\n"),
}
REQUEST = "I want to notify the customer when the payment is confirmed\nThe customer gets an email within 1 minute after the payment is confirmed\napproved\n"


def make_dataset(folder: str) -> None:
    """One project with one requirement, in the format of the tag v1.0."""
    utterance = "The system shall export monthly reports to PDF for the finance team."
    text = f"Client: {utterance}\n"
    os.makedirs(os.path.join(folder, "test-project"))
    with open(os.path.join(folder, "test-project", "transcricao_reuniao.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(folder, "test-project", "gabarito_requisitos.json"), "w", encoding="utf-8") as f:
        json.dump({"doc_id": "p", "doc_title": "P", "input": {"segments": [
            {"id": "T1", "start": 8, "end": 8 + len(utterance), "speaker": "Client", "gt_ids": ["p:R1"]}]},
            "requirements": [{"id": "p:R1", "text": utterance, "class": "FR", "subtype": None, "trace": {"reachable": True}}]}, f)
    with open(os.path.join(folder, "indice_documentos.csv"), "w", encoding="utf-8") as f:
        f.write("nome_projeto,requisitos_dialogo,qualidade_tier,palavras_transcricao,arquivo_transcricao\n")
        f.write("P,1,A,12,test-project/transcricao_reuniao.txt\n")


class BrokenValidation:
    def __init__(self, how: tuple[str, str]):
        self.tmp = tempfile.TemporaryDirectory()
        self.code = os.path.join(self.tmp.name, "code")
        os.makedirs(self.code)
        shutil.copy(os.path.join(ROOT, "main.py"), self.code)
        shutil.copytree(os.path.join(ROOT, "gigabrain"), os.path.join(self.code, "gigabrain"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        with open(os.path.join(self.code, how[0]), "a", encoding="utf-8") as f:
            f.write(how[1])
        self.data = os.path.join(self.tmp.name, "data")

    def main(self, *args, stdin: str = "") -> subprocess.CompletedProcess:
        return self.python("main.py", *args, stdin=stdin)

    def python(self, *args, stdin: str = "") -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
        return subprocess.run([sys.executable, *args], cwd=self.code, input=stdin, capture_output=True, text=True,
                              encoding="utf-8", env=env, timeout=120)


class TestBrokenValidation(unittest.TestCase):
    def each_break(self):
        for name, how in BREAKS.items():
            with self.subTest(name):
                broken = BrokenValidation(how)
                self.addCleanup(broken.tmp.cleanup)
                yield broken

    def assertOk(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_the_break_is_real(self):
        for broken in self.each_break():
            result = broken.python("-c", "import gigabrain.validation")
            self.assertNotEqual(result.returncode, 0)

    def test_chat_and_the_commands_that_read(self):
        for broken in self.each_break():
            chat = broken.main("--data", broken.data, "chat", "--simulated", "--no-validation", "--quiet", stdin=REQUEST)
            self.assertOk(chat)
            self.assertIn("Saved: R1", chat.stdout)
            listed = broken.main("--data", broken.data, "requirements")
            self.assertOk(listed)
            self.assertIn("R1", listed.stdout)
            self.assertOk(broken.main("--data", broken.data, "trace", "R1"))
            self.assertOk(broken.main("--data", broken.data, "experts"))
            minutes = os.path.join(broken.tmp.name, "minutes.md")
            with open(minutes, "w", encoding="utf-8") as f:
                f.write("# Rules\n- A declined payment can be retried up to 3 times.\n")
            self.assertOk(broken.main("--data", broken.data, "feed", "payments", minutes, "--simulated"))

    def test_demo(self):
        for broken in self.each_break():
            self.assertOk(broken.main("--data", broken.data, "demo", "--no-validation"))

    def test_evaluate(self):
        for broken in self.each_break():
            make_dataset(broken.tmp.name)
            result = broken.main("evaluate", "test-project", "--simulated", "--mode", "council,single_agent,plain_llm",
                                 "--no-validation", "--dataset", broken.tmp.name)
            self.assertOk(result)
            self.assertNotIn("ERROR", result.stdout)
            self.assertIn("MEAN", result.stdout)

    def test_the_api_serves_the_requirement_with_null_validation(self):
        script = textwrap.dedent(f"""
            import json
            from fastapi.testclient import TestClient
            from gigabrain.api import create_app
            from gigabrain.providers import SimulatedProvider
            with TestClient(create_app({{data!r}}, SimulatedProvider, validation=False)) as client:
                cid = client.post("/api/v1/conversations", json={{{{}}}}).json()["id"]
                base = f"/api/v1/conversations/{{{{cid}}}}/messages"
                for text in {REQUEST.splitlines()[:2]!r}:
                    assert client.post(base, json={{{{"text": text}}}}).status_code == 200
                assert client.post(f"/api/v1/conversations/{{{{cid}}}}/approve", json={{{{}}}}).status_code == 200
                r = client.get("/api/v1/requirements/R1")
                print(json.dumps({{{{"status": r.status_code, "validation": r.json()["validation"], "id": r.json()["id"]}}}}))
        """)
        for broken in self.each_break():
            result = broken.python("-c", script.format(data=broken.data))
            self.assertOk(result)
            self.assertEqual(json.loads(result.stdout.splitlines()[-1]), {"status": 200, "validation": None, "id": "R1"})
            self.assertIn("unavailable", result.stderr)

    def test_the_dashboard_serves_the_requirement_with_null_validation(self):
        script = textwrap.dedent("""
            import json
            from gigabrain.dashboard import server
            from gigabrain.database import Database
            db = Database({data!r})
            page = server._routes(db, {data!r}, ["requirement", "R1"], {{}})
            print(json.dumps({{"id": page["id"], "validation": page["validation"]}}))
        """)
        for broken in self.each_break():
            self.assertOk(broken.main("--data", broken.data, "chat", "--simulated", "--no-validation", "--quiet", stdin=REQUEST))
            result = broken.python("-c", script.format(data=broken.data))
            self.assertOk(result)
            self.assertEqual(json.loads(result.stdout.splitlines()[-1]), {"id": "R1", "validation": None})

    def test_asking_for_validation_fails_by_name(self):
        for broken in self.each_break():
            result = broken.main("--data", broken.data, "chat", "--simulated", stdin="exit\n")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--no-validation", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
