"""The agents of a conversation keep the PO's language, because the code tells them and checks the Twin.

The texts in Portuguese and Italian below came from real runs in which the PO wrote in English.
Nothing here calls an LLM: the model that goes off language is a script.
"""

from __future__ import annotations

import json
import tempfile
import unittest

from gigabrain.agents.leader import Leader
from gigabrain.council import Council, open_context
from gigabrain.language import phrase, spoken_language, written_in
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


class TestFixedTexts(Conversation):
    """What the code itself says to the PO follows the conversation too: "Saved: R1 v1." came in English in Portuguese."""

    def test_the_confirmation_in_portuguese(self):
        _, answers = self.talk(SimulatedProvider(), REQUEST_PT, REQUEST_PT, "aprovado")
        self.assertTrue(answers[-1]["message_to_po"].startswith("Requisitos gravados: R1 v1"), answers[-1]["message_to_po"])

    def test_the_confirmation_in_english(self):
        _, answers = self.talk(SimulatedProvider(), REQUEST_EN, REQUEST_EN, "approved")
        self.assertTrue(answers[-1]["message_to_po"].startswith("Saved: R1 v1"), answers[-1]["message_to_po"])

    def test_a_language_without_the_text_gets_english(self):
        self.assertEqual(phrase("nothing_to_approve", "French"), "There is no proposal to approve yet.")
        self.assertEqual(phrase("nothing_to_approve", None), "There is no proposal to approve yet.")
        self.assertEqual(phrase("nothing_to_approve", "Brazilian Portuguese"), "Ainda não há proposta para aprovar.")


class TestIdentifiersAreNotWords(unittest.TestCase):
    # The Twin's question to the Council in a real run in Portuguese: asked again twice for nothing.
    QUERY = ("Quais pontos concretos estão a impedir a aprovação dos requisitos P3 (listagem de nomes em ordem alfabética) e P5 "
             "(exclusão de nome registado), sobretudo a falta de suporte face às fontes citadas (c-20261104-144648, "
             "expert:gestao-de-nomes-e-identificadores, expert:portabilidade-e-ambientes-posix)? Que sugestões de correção "
             "permitem elevar o suporte acima de 30% sem alterar o âmbito?")

    def test_a_question_full_of_ids_is_still_portuguese(self):
        self.assertTrue(written_in(self.QUERY, "Portuguese"))
        self.assertFalse(written_in(self.QUERY, "English"))

    def test_only_ids_say_nothing(self):
        self.assertTrue(written_in("R1 v2, R3 v1, POSIX IEEE 1003.1-2024, expert:nomes, c-20261104-144648", "English"))


class TestKnowledgeFiles(Conversation):
    """listagem.md, edicao.md and exclusao.md were written in English in a conversation in Portuguese."""

    def test_an_expert_born_in_the_conversation_writes_in_its_language(self):
        provider = Script()
        self.talk(provider, REQUEST_PT, REQUEST_PT, "aprovado")
        for role in ("expert_create", "expert_update"):
            for received in provider.received.get(role, []):
                with self.subTest(role):
                    self.assertEqual(received["language"], "Portuguese")
        self.assertIn("expert_create", provider.received)

    def test_a_meeting_keeps_its_prompts(self):
        provider = Script()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctx = open_context(tmp.name, provider, validation=False)
        self.addCleanup(ctx.db.close)
        Leader(ctx).consult_draft({"topic": "accounts", "statement": "The system shall export reports.", "class": "FR",
                                   "subtype": None, "turns": []}, {})
        self.assertNotIn("language", provider.received["expert_create"][0])


class TestAcceptanceCriteria(unittest.TestCase):
    # Criteria of real proposals in Portuguese, joined as the Twin's check joins them: each one was asked again
    # for nothing, because the words Portuguese shares with Spanish and Italian do not count. "o" now does.
    CRITERIA_PT = [
        "a lista contém 'Ana' o usuário tenta alterar 'Ana' para 'Ana Maria' o sistema rejeita a alteração e a lista "
        "continua contendo 'Ana' a lista contém 'Ana' o usuário exclui 'Ana' e registra 'Ana Maria' a lista passa a conter 'Ana Maria'",
        "que o usuário informa o nome \"Ana\" confirma o cadastro o sistema registra \"Ana\" e ele passa a aparecer na listagem "
        "que o campo nome está vazio tenta confirmar o cadastro o sistema recusa o cadastro e exibe mensagem de que o nome é obrigatório",
    ]
    SPANISH = "Cuando el usuario registra un nombre, el sistema debe almacenar el nombre informado o rechazarlo si está vacío."

    def test_criteria_in_portuguese_are_portuguese(self):
        for text in self.CRITERIA_PT:
            with self.subTest(text[:30]):
                self.assertTrue(written_in(text, "Portuguese"))
                self.assertFalse(written_in(text, "English"))

    def test_one_shared_word_does_not_make_spanish_portuguese(self):
        self.assertFalse(written_in(self.SPANISH, "Portuguese"))
