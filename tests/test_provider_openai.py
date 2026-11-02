"""The real provider with the installed openai SDK, against a local server. No paid call.

What it protects: the requests the SDK sends, and what the provider does with answers that are
not a JSON object, whatever version of the SDK `requirements.txt` brings.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gigabrain.agents import DigitalTwin
from gigabrain.council import open_context
from gigabrain.providers import InvalidAnswer, OpenAICompatibleProvider, ProviderError

KEY_VARIABLE = "GIGABRAIN_TEST_KEY"


def completion(content) -> dict:
    return {"id": "x", "object": "chat.completion", "created": 1, "model": "test-model",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}


class FakeServer:
    """Answers each request with the next of `answers`: (status, body)."""

    def __init__(self, answers: list[tuple[int, dict]]):
        self.answers = list(answers)
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization")})
                status, answer = outer.answers.pop(0)
                data = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class TestRealProvider(unittest.TestCase):
    def provider(self, *answers) -> tuple[OpenAICompatibleProvider, FakeServer]:
        server = FakeServer(list(answers))
        self.addCleanup(server.close)
        os.environ[KEY_VARIABLE] = "test-key"
        self.addCleanup(os.environ.pop, KEY_VARIABLE, None)
        return OpenAICompatibleProvider(server.url, "test-model", KEY_VARIABLE), server

    def test_the_request_the_sdk_sends(self):
        provider, server = self.provider((200, completion('{"ok": true}')))
        answer = provider.complete_json("twin", "system text", [{"role": "user", "content": "{}"}])
        self.assertEqual(answer, {"ok": True})
        sent = server.requests[0]
        self.assertTrue(sent["path"].endswith("/chat/completions"))
        self.assertEqual(sent["authorization"], "Bearer test-key")
        self.assertEqual(sent["body"]["model"], "test-model")
        self.assertEqual(sent["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(sent["body"]["max_tokens"], 8192)
        self.assertEqual(sent["body"]["messages"][0], {"role": "system", "content": "system text"})
        self.assertNotIn("temperature", sent["body"])
        self.assertEqual(provider.last_usage, {"tokens_in": 11, "tokens_out": 7})

    def test_temperature_goes_only_when_asked_for(self):
        provider, server = self.provider((200, completion("{}")))
        provider.complete_json("critic_judge", "s", [{"role": "user", "content": "{}"}], temperature=0)
        self.assertEqual(server.requests[0]["body"]["temperature"], 0)

    def test_invalid_json_is_asked_again_once(self):
        provider, server = self.provider((200, completion("not json")), (200, completion('{"ok": 1}')))
        self.assertEqual(provider.complete_json("twin", "s", [{"role": "user", "content": "{}"}]), {"ok": 1})
        self.assertEqual(len(server.requests), 2)
        self.assertEqual(server.requests[1]["body"]["messages"][-2], {"role": "assistant", "content": "not json"})

    def test_json_that_is_not_an_object_counts_as_invalid(self):
        provider, server = self.provider((200, completion("[1, 2]")), (200, completion("null")))
        with self.assertRaises(InvalidAnswer):
            provider.complete_json("twin", "s", [{"role": "user", "content": "{}"}])
        self.assertEqual(len(server.requests), 2)

    def test_empty_content_counts_as_invalid(self):
        provider, _ = self.provider((200, completion(None)), (200, completion("")))
        with self.assertRaises(InvalidAnswer):
            provider.complete_json("twin", "s", [{"role": "user", "content": "{}"}])

    def test_an_api_error_is_not_an_invalid_answer(self):
        provider, _ = self.provider((400, {"error": {"message": "bad request"}}))
        with self.assertRaises(Exception) as caught:
            provider.complete_json("twin", "s", [{"role": "user", "content": "{}"}])
        self.assertNotIsInstance(caught.exception, ProviderError)

    def test_without_the_key_it_says_so(self):
        os.environ.pop(KEY_VARIABLE, None)
        with self.assertRaisesRegex(ProviderError, KEY_VARIABLE):
            OpenAICompatibleProvider("http://127.0.0.1:1", "m", KEY_VARIABLE)


class TestInvalidAnswerInTheFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def twin(self, provider) -> DigitalTwin:
        ctx = open_context(self.tmp.name, provider, validation=False)
        self.addCleanup(ctx.db.close)
        ctx.conversation_id = ctx.db.start_conversation("after_meeting")
        return DigitalTwin(ctx)

    def test_it_is_logged_and_the_flow_goes_on(self):
        class Answers:
            name, last_usage = "fake", {}

            def complete_json(self, role, system, messages, **options):
                raise InvalidAnswer(f"{role}: the model did not return valid JSON: oops")

        twin = self.twin(Answers())
        decision = twin.hear_po("I want something")
        self.assertEqual(decision["action"], "ask")
        self.assertTrue(decision["message_to_po"])
        kinds = [e["kind"] for e in twin.ctx.db.list_events(twin.ctx.conversation_id)]
        self.assertIn("error", kinds)
        self.assertIn("llm_call", kinds)  # the call was spent

    def test_a_failure_of_the_api_still_reaches_the_caller(self):
        class Down:
            name, last_usage = "fake", {}

            def complete_json(self, role, system, messages, **options):
                raise ConnectionError("no network")

        with self.assertRaises(ConnectionError):
            self.twin(Down()).hear_po("I want something")


if __name__ == "__main__":
    unittest.main()
