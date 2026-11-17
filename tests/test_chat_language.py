"""The agents of a conversation keep the PO's language, because the code tells them and checks the Twin.

The texts in Portuguese and Italian below came from real runs in which the PO wrote in English.
Nothing here calls an LLM: the model that goes off language is a script.
"""

from __future__ import annotations

import json
import tempfile
import unittest

from gigabrain.council import Council, open_context
from gigabrain.language import spoken_language, written_in
from gigabrain.providers import SimulatedProvider

REQUEST_EN = "I need a requirement that allows registered users to change their email, but only once every 30 days"
REQUEST_PT = "Preciso de um requisito que permita a usuários registrados alterar o email, mas apenas uma vez a cada 30 dias"
REQUEST_FR = "Nous voulons que l'utilisateur inscrit puisse modifier l'adresse email du compte une seule fois en 30 jours"
REQUEST_ES = "Necesito un requisito que permita a los usuarios cambiar su correo, pero solo una vez cada 30 días"

QUERY_PT = ("Existe algum requisito aprovado sobre edição de perfil do usuário, especificamente alteração de e-mail, "
            "ou sobre limites de alterações de dados de conta?")
QUERY_EN = "Is there an approved requirement about changing the account email or about limits on how often it can change?"
ANSWER_IT = ("Nel mio file di conoscenza non esiste alcun requisito approvato che imponga un limite di 30 giorni "
             "o altre restrizioni di frequenza sulle modifiche ai dati di profilo.")
MESSAGE_PT = ("Proposta de requisito para alteração de e-mail: usuário registrado pode alterar, mas só uma vez a cada 30 dias. "
              "Os critérios cobrem o caminho permitido, o bloqueio dentro de 30 dias, o e-mail já usado por outra conta e o "
              "primeiro uso. Confirme se esse comportamento atende, em especial o que fazer quando o novo e-mail já estiver em uso.")


class TestSpokenLanguage(unittest.TestCase):
    def test_a_request_tells_its_language(self):
        self.assertEqual(spoken_language(REQUEST_EN), "English")
        self.assertEqual(spoken_language(REQUEST_PT), "Portuguese")

    def test_a_short_answer_of_someone_who_answers_by_number(self):
        self.assertEqual(spoken_language("1. Yes; 3. it shows the date"), "English")
        self.assertEqual(spoken_language("1. Sim; 3. o sistema deve mostrar uma mensagem"), "Portuguese")

    def test_it_does_not_guess(self):
        for text in ("yes", "ok", "sim", "approved", "1, 2 e 3", ""):
            with self.subTest(text):
                self.assertIsNone(spoken_language(text))

    def test_other_languages_are_not_taken_for_portuguese(self):
        self.assertIsNone(spoken_language(REQUEST_FR))
        self.assertIsNone(spoken_language(REQUEST_ES))


class TestWrittenIn(unittest.TestCase):
    def test_the_language_it_should_be_in(self):
        self.assertTrue(written_in(QUERY_EN, "English"))
        self.assertTrue(written_in(QUERY_PT, "Portuguese"))

    def test_another_language(self):
        self.assertFalse(written_in(QUERY_PT, "English"))
        self.assertFalse(written_in(QUERY_EN, "Portuguese"))

    def test_a_language_it_has_no_words_for(self):
        self.assertFalse(written_in(ANSWER_IT, "English"))

    def test_a_real_answer_in_portuguese_is_not_a_false_alarm(self):
        # At 10% of markers, this message made the Twin answer again for nothing in the first real run in Portuguese.
        self.assertTrue(written_in(MESSAGE_PT, "Portuguese"))
        self.assertFalse(written_in(MESSAGE_PT, "English"))

    def test_other_languages_do_not_pass_for_either(self):
        for text in (ANSWER_IT, REQUEST_FR + " " + REQUEST_FR, REQUEST_ES + " " + REQUEST_ES):
            with self.subTest(text[:20]):
                self.assertFalse(written_in(text, "English"))
                self.assertFalse(written_in(text, "Portuguese"))

    def test_too_short_to_tell(self):
        self.assertTrue(written_in("Quais dados são editáveis?", "English"))

    def test_loanwords_do_not_change_the_language(self):
        self.assertTrue(written_in("O sistema deve aplicar rate limiting com cooldown em cada request de alteração da conta.", "Portuguese"))


class Script(SimulatedProvider):
    """The simulator, keeping what each role received; `queries` are the Twin's consultations, in order."""

    def __init__(self, queries=()):
        super().__init__()
        self.queries = list(queries)
        self.received: dict[str, list[dict]] = {}

    def complete_json(self, role, system, messages, temperature=None):
        self.received.setdefault(role, []).append(json.loads(messages[-1]["content"]))
        return super().complete_json(role, system, messages, temperature)

    def _twin(self, messages):
        answer = super()._twin(messages)
        if answer["action"] == "consult" and self.queries:
            answer["query"]["question"] = self.queries.pop(0)
        return answer

    def _leader_decompose(self, messages):
        answer = super()._leader_decompose(messages)
        if len(answer["subqueries"]) == 1:  # two experts, so that the Leader has to consolidate
            answer["subqueries"].append({**answer["subqueries"][0], "topic": "accounts", "expert_id": None})
        return answer


class Conversation(unittest.TestCase):
    def talk(self, provider, *texts):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctx = open_context(tmp.name, provider, validation=False)
        self.addCleanup(ctx.db.close)
        council = Council(ctx)
        council.start()
        answers = [council.talk(text) for text in texts]
        return ctx, answers

    @staticmethod
    def kinds(ctx) -> list[str]:
        return [e["kind"] for e in ctx.db.list_events(ctx.conversation_id, limit=5000)]

    @staticmethod
    def queries(ctx) -> list[str]:
        return [e["content"].get("question") for e in ctx.db.list_events(ctx.conversation_id, limit=5000)
                if e["kind"] == "query" and e["sender"] == "twin"]


class TestEveryAgentIsTold(Conversation):
    def test_the_twin_the_leader_and_the_experts(self):
        provider = Script()
        self.talk(provider, REQUEST_EN)
        for role in ("twin", "leader_decompose", "expert_answer", "leader_consolidate"):
            with self.subTest(role):
                self.assertTrue(provider.received[role])
                self.assertTrue(all(data["language"] == "English" for data in provider.received[role]))

    def test_a_short_answer_keeps_the_language_of_the_conversation(self):
        provider = Script()
        self.talk(provider, REQUEST_PT, "sim")
        self.assertEqual(provider.received["twin"][-1]["language"], "Portuguese")

    def test_the_po_can_change_language(self):
        provider = Script()
        self.talk(provider, REQUEST_PT, REQUEST_EN)
        self.assertEqual(provider.received["leader_decompose"][0]["language"], "Portuguese")
        self.assertEqual(provider.received["twin"][-1]["language"], "English")

    def test_in_another_language_nothing_is_said_and_nothing_is_checked(self):
        provider = Script()
        ctx, _ = self.talk(provider, REQUEST_FR)
        for role in ("twin", "leader_decompose", "expert_answer"):
            self.assertNotIn("language", provider.received[role][0])
        self.assertNotIn("language_retry", self.kinds(ctx))


class TestTheTwinIsChecked(Conversation):
    def test_a_query_in_another_language_is_asked_again_before_it_reaches_the_leader(self):
        provider = Script([QUERY_PT, QUERY_EN])
        ctx, _ = self.talk(provider, REQUEST_EN)
        self.assertEqual(self.queries(ctx), [QUERY_EN])
        self.assertEqual(provider.received["leader_decompose"][0]["question"], QUERY_EN)
        self.assertEqual(self.kinds(ctx).count("language_retry"), 1)
        self.assertNotIn("language_off", self.kinds(ctx))

    def test_it_is_asked_only_once_and_the_conversation_goes_on(self):
        provider = Script([QUERY_PT, QUERY_PT])
        ctx, answers = self.talk(provider, REQUEST_EN)
        self.assertEqual(self.queries(ctx), [QUERY_PT])
        self.assertEqual(self.kinds(ctx).count("language_retry"), 1)
        self.assertEqual(self.kinds(ctx).count("language_off"), 1)
        self.assertEqual(answers[0]["action"], "ask")
        # The field comes from the code, so the rest of the chain is still told the right language.
        self.assertEqual(provider.received["leader_decompose"][0]["language"], "English")

    def test_an_answer_in_the_right_language_costs_nothing_more(self):
        provider = Script()
        ctx, _ = self.talk(provider, REQUEST_EN)
        self.assertEqual(len(provider.received["twin"]), 2)  # consult, then ask
        self.assertNotIn("language_retry", self.kinds(ctx))

    def test_the_simulator_does_not_change_its_mind_when_asked_again(self):
        # It answers in English, whatever the PO writes: asking again must not turn a question into a proposal.
        ctx, answers = self.talk(Script(), REQUEST_PT)
        self.assertEqual(answers[0]["action"], "ask")
        self.assertIn("language_retry", self.kinds(ctx))


if __name__ == "__main__":
    unittest.main()
