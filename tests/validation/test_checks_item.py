"""Tests of the item-level checks.

Every test has the same design: it starts from a clean artefact, plants **one**
defect and requires that (a) the right check fires and (b) the clean artefact
fires nothing. Testing the silence matters as much as testing the alarm: a check
that reports everything is as useless as one that reports nothing.
"""

from __future__ import annotations

import unittest

from gigabrain.validation.engine import ValidationEngine
from gigabrain.validation.model import ArtifactKind, Criterion, LinkRef, RequirementType, Severity, Story

from .helpers import CLEAN_CRITERIA, artifact, check_ids, envelope, evidence, quote, source, user_story


class ItemCheckCase(unittest.TestCase):
    """Base with the shortcut that validates a single artefact."""

    def findings_for(self, art, *others, sources=None, glossary=None, config=None, trace=None):
        env = envelope(art, *others, sources=sources, glossary=glossary)
        engine = ValidationEngine(config=config, trace=trace)
        return engine.validate_artifact(art, env).findings

    def severities(self, findings, check_id):
        return [f.severity for f in findings if f.check_id == check_id]


class TestCleanArtefacts(ItemCheckCase):
    def test_a_structured_requirement_fires_nothing(self):
        findings = self.findings_for(artifact())
        self.assertEqual(findings, [], [f.short() for f in findings])

    def test_a_portuguese_user_story_fires_nothing(self):
        findings = self.findings_for(user_story())
        self.assertEqual(findings, [], [f.short() for f in findings])


class TestStructure(ItemCheckCase):
    def test_no_title(self):
        self.assertIn("structure.required_fields", check_ids(self.findings_for(artifact(title=""))))

    def test_a_requirement_without_a_statement_is_blocking(self):
        findings = self.findings_for(artifact(statement=""))
        self.assertEqual(self.severities(findings, "structure.required_fields"), [Severity.BLOCKER])

    def test_statement_that_is_not_a_shall_sentence(self):
        findings = self.findings_for(artifact(statement="Reports of the same topic would be nice in one item.", ears_pattern=""))
        self.assertEqual(self.severities(findings, "structure.ears_statement"), [Severity.MAJOR])

    def test_declared_ears_pattern_that_does_not_match(self):
        findings = self.findings_for(artifact(ears_pattern="ubiquitous"))
        self.assertEqual(self.severities(findings, "structure.ears_statement"), [Severity.MINOR])

    def test_portuguese_shall_sentence_is_accepted(self):
        art = artifact(statement="Quando um tópico recebe a terceira denúncia, o sistema deve criar um item na fila de revisão.",
                       evidences=[])
        self.assertNotIn("structure.ears_statement", check_ids(self.findings_for(art)))

    def test_requirement_without_a_story(self):
        findings = self.findings_for(artifact(story=None))
        self.assertEqual(self.severities(findings, "structure.story_format"), [Severity.MAJOR])

    def test_story_with_a_missing_part(self):
        findings = self.findings_for(artifact(story=Story(role="forum moderator", goal="to group the reports", benefit="")))
        self.assertEqual(self.severities(findings, "structure.story_format"), [Severity.MINOR])

    def test_plain_story_outside_the_template(self):
        findings = self.findings_for(user_story(story="O sistema precisa agrupar denúncias."))
        self.assertIn("structure.story_format", check_ids(findings))

    def test_plain_story_in_english(self):
        art = user_story(story="As a forum moderator, I want to group reports, so that I decide once per topic.")
        self.assertNotIn("structure.story_format", check_ids(self.findings_for(art)))

    def test_no_acceptance_criteria_is_blocking(self):
        findings = self.findings_for(artifact(criteria=[]))
        self.assertEqual(self.severities(findings, "structure.acceptance_criteria"), [Severity.BLOCKER])

    def test_a_single_criterion_is_only_information(self):
        # This layer cannot tell whether the source has an exception case;
        # asking for a second criterion made the writer invent one.
        findings = self.findings_for(artifact(criteria=CLEAN_CRITERIA[:1]))
        self.assertEqual(self.severities(findings, "structure.acceptance_criteria"), [Severity.INFO])

    def test_gherkin_criterion_without_a_then(self):
        broken = Criterion(scenario="Third report", given="a topic with two reports", when="a third user reports it", then="")
        findings = self.findings_for(artifact(criteria=[broken, CLEAN_CRITERIA[1]]))
        self.assertEqual(self.severities(findings, "structure.gherkin_criteria"), [Severity.MAJOR])

    def test_an_enabler_needs_only_one_criterion_and_a_reason(self):
        art = artifact(kind=ArtifactKind.TECHNICAL_ENABLER, story=None, criteria=CLEAN_CRITERIA[:1],
                       rationale="There is no review queue in the product yet.")
        ids = check_ids(self.findings_for(art))
        self.assertNotIn("structure.acceptance_criteria", ids)
        self.assertNotIn("structure.enabler_format", ids)
        self.assertNotIn("structure.story_format", ids)

    def test_an_enabler_without_a_reason(self):
        art = artifact(kind=ArtifactKind.TECHNICAL_ENABLER, story=None, criteria=CLEAN_CRITERIA[:1], rationale="")
        self.assertIn("structure.enabler_format", check_ids(self.findings_for(art)))

    def test_shall_statement_outside_the_incose_pattern(self):
        art = user_story(kind=ArtifactKind.SHALL_STATEMENT, story="", statement="Desempenho bom na fila.",
                         criteria=["Quando há 500 tópicos, o sistema responde em até 2 segundos."])
        self.assertIn("structure.shall_pattern", check_ids(self.findings_for(art)))

    def test_shall_statement_in_the_incose_pattern(self):
        art = user_story(kind=ArtifactKind.SHALL_STATEMENT, story="",
                         statement="A fila de revisão deve carregar a primeira página em até 2 segundos.",
                         criteria=["Quando há 500 tópicos, o sistema responde em até 2 segundos."])
        self.assertNotIn("structure.shall_pattern", check_ids(self.findings_for(art)))

    def test_nfr_with_an_unknown_subtype(self):
        art = artifact(req_type=RequirementType.NON_FUNCTIONAL, nfr_subtype="speed")
        self.assertIn("structure.classification", check_ids(self.findings_for(art)))

    def test_nfr_with_a_known_subtype(self):
        for subtype in ("performance", "safety", "other"):
            art = artifact(req_type=RequirementType.NON_FUNCTIONAL, nfr_subtype=subtype)
            self.assertNotIn("structure.classification", check_ids(self.findings_for(art)), subtype)

    def test_requirement_without_a_class(self):
        self.assertIn("structure.classification", check_ids(self.findings_for(artifact(req_type=RequirementType.UNKNOWN))))


class TestAmbiguity(ItemCheckCase):
    def test_subjective_term_in_the_statement(self):
        art = artifact(statement="When a topic receives the third report, the system shall create one item fast.")
        self.assertEqual(self.severities(self.findings_for(art), "ambiguity.vague_terms"), [Severity.MAJOR])

    def test_a_number_makes_the_term_acceptable(self):
        # Same rule as requirement_format.vague_terms: "fast" with a number is not vague.
        art = artifact(statement="When a topic receives the third report, the system shall create one item fast, within 2 seconds.")
        self.assertNotIn("ambiguity.vague_terms", check_ids(self.findings_for(art)))

    def test_subjective_benefit_in_the_story_is_allowed(self):
        art = artifact(story=Story(role="forum moderator", goal="to see the reports of a topic as one item",
                                   benefit="reviewing feels fast and easy"))
        self.assertNotIn("ambiguity.vague_terms", check_ids(self.findings_for(art)))

    def test_inflected_portuguese_term(self):
        # "intuitiva", feminine, has to be caught as much as "intuitivo".
        art = user_story(story="Como moderador do fórum, quero uma fila intuitiva, para que eu erre menos.")
        self.assertIn("ambiguity.vague_terms", check_ids(self.findings_for(art)))

    def test_optionality(self):
        art = artifact(statement="When a topic receives the third report, the system shall create one item if possible.")
        self.assertIn("ambiguity.optionality", check_ids(self.findings_for(art)))

    def test_generic_role_when_the_source_names_one(self):
        # The default turn says "the moderator is told only once".
        art = artifact(story=Story(role="user", goal="to see the reports of a topic as one item", benefit="I decide once per topic"))
        findings = self.findings_for(art)
        self.assertEqual(self.severities(findings, "ambiguity.generic_persona"), [Severity.MINOR])
        self.assertIn("moderator", next(f for f in findings if f.check_id == "ambiguity.generic_persona").message)

    def test_generic_role_when_the_source_names_none(self):
        # "user" is all the source supports; asking for more is asking for a persona.
        art = artifact(story=Story(role="user", goal="to see the reports of a topic as one item", benefit="I decide once per topic"))
        plain = source(text="When a topic receives the third report it goes to the review queue as one item.")
        findings = self.findings_for(art, sources=[plain])
        self.assertEqual(self.severities(findings, "ambiguity.generic_persona"), [Severity.INFO])

    def test_a_vague_term_the_source_itself_used_is_information(self):
        # From the dataset meetings: "adequate response time" became "under 2 seconds".
        said = source(text="The page must have an adequate response time when a topic receives the third report.")
        art = artifact(statement="When a topic receives the third report, the system shall show it with an adequate response time.")
        findings = self.findings_for(art, sources=[said])
        self.assertEqual(self.severities(findings, "ambiguity.vague_terms"), [Severity.INFO])
        self.assertIn("The source itself is vague", next(f for f in findings if f.check_id == "ambiguity.vague_terms").message)

    def test_a_saved_requirement_does_not_vouch_for_itself(self):
        # Regression, 20/10/2026: once saved, a requirement's own words are in the knowledge
        # of the expert it cites. That line must not make its vagueness look inherited.
        knowledge = source("expert:search", kind="expert", origin="knowledge of the search expert",
                           text="## Approved requirements\n- R1 (NFR) — The page shall show it with an adequate response time.")
        art = artifact("R1", statement="When a topic receives the third report, the system shall show it with an adequate response time.",
                       evidences=[evidence(), evidence("expert:search")])
        findings = self.findings_for(art, sources=[source(), knowledge])
        self.assertEqual(self.severities(findings, "ambiguity.vague_terms"), [Severity.MAJOR])

    def test_a_vague_term_the_writer_added_is_still_major(self):
        art = artifact(statement="When a topic receives the third report, the system shall show it with an adequate response time.")
        self.assertEqual(self.severities(self.findings_for(art), "ambiguity.vague_terms"), [Severity.MAJOR])

    def test_generic_role_in_a_plain_portuguese_story(self):
        art = user_story(story="Como usuário, quero agrupar denúncias, para que eu decida uma vez por tópico.")
        self.assertIn("ambiguity.generic_persona", check_ids(self.findings_for(art)))

    def test_quantifier_without_a_quantity(self):
        art = artifact(statement="When a topic receives many reports, the system shall create one item in the review queue.",
                       criteria=[Criterion("Many", "a topic", "users report it", "the system creates one item"), CLEAN_CRITERIA[1]])
        self.assertIn("ambiguity.unquantified", check_ids(self.findings_for(art)))

    def test_a_quantifier_the_source_itself_used_is_information(self):
        said = source(text="When a topic receives many reports from users it goes to the review queue as one item.")
        art = artifact(statement="When a topic receives many reports, the system shall create one item in the review queue.",
                       criteria=[Criterion("Many", "a topic", "users report it", "the system creates one item"), CLEAN_CRITERIA[1]])
        self.assertEqual(self.severities(self.findings_for(art, sources=[said]), "ambiguity.unquantified"), [Severity.INFO])

    def test_quantifier_with_a_quantity_does_not_fire(self):
        art = artifact(statement="When a topic receives many reports, at least 3 times in 1 hour, the system shall create one item in the review queue.")
        self.assertNotIn("ambiguity.unquantified", check_ids(self.findings_for(art)))

    def test_absolute_generalisation(self):
        art = artifact(statement="The system shall always create one item in the review queue.", ears_pattern="ubiquitous")
        self.assertIn("ambiguity.overgeneralization", check_ids(self.findings_for(art)))


class TestVerifiability(ItemCheckCase):
    def test_then_clause_without_an_observable_effect(self):
        empty = Criterion("Main flow", "the system is in normal operation", "the user performs the action",
                          "the system behaves as the PO described")
        findings = self.findings_for(artifact(criteria=[empty, CLEAN_CRITERIA[1]]))
        self.assertEqual(self.severities(findings, "verifiability.criterion_observable"), [Severity.MAJOR])

    def test_then_clause_with_a_quantity_is_observable(self):
        timed = Criterion("Timing", "500 topics waiting", "the moderator opens the queue", "the first page is ready within 2 seconds")
        self.assertNotIn("verifiability.criterion_observable", check_ids(self.findings_for(artifact(criteria=[timed, CLEAN_CRITERIA[1]]))))

    def test_plain_criterion_without_condition_or_effect(self):
        art = user_story(criteria=["A fila precisa estar em ordem.", "Quando o item é criado, o sistema notifica o moderador."])
        self.assertIn("verifiability.criterion_observable", check_ids(self.findings_for(art)))

    def test_if_in_the_middle_of_the_sentence_does_not_make_it_observable(self):
        # Regression: "se possível" has "se" and once passed for a conditional.
        art = user_story(criteria=["A fila deve ser ágil se possível.", "Quando o item é criado, o sistema notifica o moderador."])
        self.assertIn("verifiability.criterion_observable", check_ids(self.findings_for(art)))

    def test_plain_criterion_with_an_observable_verb(self):
        art = user_story(criteria=["O sistema envia uma notificação ao moderador de plantão.", "Quando o item é criado, o sistema notifica o moderador."])
        self.assertNotIn("verifiability.criterion_observable", check_ids(self.findings_for(art)))

    def test_portuguese_effects_written_by_an_llm(self):
        # Regression, 20/10/2026: in the first run with DeepSeek "aparece" was not recognised
        # and a sound criterion was rejected.
        for then in ("a loja aparece na listagem de lojas em atenção com a contagem de 3 reclamações",
                     "o sistema nega o acesso", "o sistema bloqueia a loja", "a loja permanece na listagem",
                     "o sistema devolve a lista vazia"):
            with self.subTest(then=then):
                crit = Criterion("Fluxo", "uma loja com 2 reclamações", "uma nova reclamação é registrada", then)
                findings = self.findings_for(artifact(criteria=[crit, CLEAN_CRITERIA[1]]))
                self.assertNotIn("verifiability.criterion_observable", check_ids(findings))

    def test_english_effects_written_by_an_llm(self):
        # Regression, 20/10/2026: 87 Then clauses of the DeepSeek meetings were rejected,
        # most of them with a plain effect the English lexicon did not have.
        for then in ("the system indicates which colleges are not yet covered", "the server responds within 2 seconds",
                     "the site passes its test suite in Firefox", "all tested functions work in Netscape",
                     "the page conforms to the OUS site standards", "all pages follow the OUS conventions"):
            with self.subTest(then=then):
                crit = Criterion("Flow", "a page", "the student opens it", then)
                self.assertNotIn("verifiability.criterion_observable",
                                 check_ids(self.findings_for(artifact(criteria=[crit, CLEAN_CRITERIA[1]]))))

    def test_filler_that_looks_like_an_effect(self):
        for then in ("the system behaves as follows", "the system behaves as required", "the review", "the use of the degree"):
            with self.subTest(then=then):
                crit = Criterion("Flow", "a page", "the student opens it", then)
                self.assertIn("verifiability.criterion_observable",
                              check_ids(self.findings_for(artifact(criteria=[crit, CLEAN_CRITERIA[1]]))))

    def test_nouns_that_look_like_verbs_are_not_effects(self):
        for then in ("a conta do usuário", "a marca da loja", "a entrada principal"):
            with self.subTest(then=then):
                crit = Criterion("Fluxo", "uma loja", "o usuário abre a página", then)
                findings = self.findings_for(artifact(criteria=[crit, CLEAN_CRITERIA[1]]))
                self.assertIn("verifiability.criterion_observable", check_ids(findings))

    def test_portuguese_action_stated_by_the_grammar(self):
        # Regression, 16/11/2026: in the real conversation of 04/11 none of these had a verb of the
        # lexicon, every proposal was returned, and the PO approved with two requirements rejected.
        for then in ("o sistema termina com código de saída diferente de zero",
                     "o sistema encerra sem executar as funções de cadastro, listagem, edição e exclusão de nomes",
                     "o sistema não oferece nenhuma operação de edição de nomes",
                     "nenhuma dependência fora do escopo da última versão POSIX é utilizada",
                     "o nome fica registado e passa a constar da listagem",
                     "a aplicação inicia e executa as operações de registro sem erro de incompatibilidade"):
            with self.subTest(then=then):
                crit = Criterion("Fluxo", "um ambiente POSIX", "o sistema é iniciado", then)
                self.assertNotIn("verifiability.criterion_observable",
                                 check_ids(self.findings_for(artifact(criteria=[crit, CLEAN_CRITERIA[1]]))))

    def test_portuguese_state_or_expectation_is_not_an_action(self):
        for then in ("o sistema não é suportado nesse ambiente", "o sistema não é declarado suportado",
                     "a compatibilidade é declarada apenas para POSIX", "o sistema não é suportado e pode falhar ao iniciar",
                     "o sistema não garante a execução das operações", "o sistema sempre funciona nesse ambiente",
                     "o sistema que foi descrito", "o resultado é considerado correto"):
            with self.subTest(then=then):
                crit = Criterion("Fluxo", "um ambiente", "o sistema é iniciado", then)
                self.assertIn("verifiability.criterion_observable",
                              check_ids(self.findings_for(artifact(criteria=[crit, CLEAN_CRITERIA[1]]))))

    def test_a_literal_expected_value_is_observable(self):
        # The test compares what it sees with the value, whatever the verb (16/11/2026).
        for then in ('a linha 1 da saída padrão é "Ana"', "the exit code is 0", "the response status is HTTP 404",
                     "as linhas são 'Ana', 'Bruno' e 'Carlos', por esta ordem", "o sistema termina cada uma das 4 operações com código de saída 0"):
            with self.subTest(then=then):
                crit = Criterion("Flow", "three names", "the user lists them", then)
                self.assertNotIn("verifiability.criterion_observable",
                                 check_ids(self.findings_for(artifact(criteria=[crit, CLEAN_CRITERIA[1]]))))

    def test_a_number_that_refers_to_a_document_is_not_a_value(self):
        for then in ("the system behaves as described in section 3.2", "o sistema se comporta conforme o requisito 4",
                     "the result is as in step 2 of version 1.1", "the user's needs and the admin's needs are met"):
            with self.subTest(then=then):
                crit = Criterion("Flow", "a page", "the user opens it", then)
                self.assertIn("verifiability.criterion_observable",
                              check_ids(self.findings_for(artifact(criteria=[crit, CLEAN_CRITERIA[1]]))))

    def test_criterion_with_a_subjective_term(self):
        vague = Criterion("Speed", "a reported topic", "the moderator opens it", "the system answers fast")
        findings = self.findings_for(artifact(criteria=[vague, CLEAN_CRITERIA[1]]))
        self.assertIn("verifiability.criterion_not_vague", check_ids(findings))
        self.assertNotIn("ambiguity.vague_terms", check_ids(findings))  # reported once, in its own dimension

    def test_then_clause_that_repeats_the_statement(self):
        echo = Criterion("Main flow", "the system is in normal operation", "the situation happens",
                         "when a topic receives the third report, the system shall create one item in the review queue")
        findings = self.findings_for(artifact(criteria=[echo, CLEAN_CRITERIA[1]]))
        self.assertEqual(self.severities(findings, "verifiability.criterion_restates"), [Severity.MINOR])


class TestNumberSupported(ItemCheckCase):
    """A number written with digits in the statement must be in a cited source (16/11/2026)."""

    def test_a_number_the_source_does_not_give_is_major(self):
        art = artifact(statement="When a topic receives the third report, the system shall create one item in the review queue within 2 seconds.")
        findings = self.findings_for(art)
        self.assertEqual(self.severities(findings, "grounding.number_supported"), [Severity.MAJOR])
        self.assertEqual([f.excerpt for f in findings if f.check_id == "grounding.number_supported"], ["2"])

    def test_a_number_the_source_gives_in_digits_or_in_words(self):
        said = source(text="We need the queue to show the item within 2 seconds, and at most ten items per page.")
        art = artifact(statement="When a topic is reported, the system shall show the item within 2 seconds, 10 items per page.")
        self.assertNotIn("grounding.number_supported", check_ids(self.findings_for(art, sources=[said])))

    def test_a_saved_requirement_does_not_vouch_for_its_own_number(self):
        # The expert's knowledge records saved requirements ("R1 (NFR) — ..."); those lines were written by the system.
        own = source("expert:queue", text="Rules of the queue.\nR1 (NFR) — The queue shall load within 2 seconds.", kind="expert")
        art = artifact(statement="The queue shall load within 2 seconds.", evidences=[evidence("expert:queue")])
        self.assertIn("grounding.number_supported", check_ids(self.findings_for(art, sources=[own])))

    def test_a_number_at_the_end_of_a_sentence_is_read_in_the_source_and_in_the_statement(self):
        # 17/11/2026, live run: the expert wrote "(ex.: IEEE 1003.1, versões)" and the statement
        # "(IEEE 1003.1-2017)" was returned for a number the source has.
        said = source(text="The queue must show the item within 2. And the standard is IEEE 1003.1, any edition.")
        art = artifact(statement="The system shall show the item within 2 seconds, as IEEE 1003.1-2017 says.")
        self.assertEqual([f.excerpt for f in self.findings_for(art, sources=[said])
                          if f.check_id == "grounding.number_supported"], ["2017"])
        art = artifact(statement="When a topic is reported, the system shall show the item within 3.")
        self.assertEqual([f.excerpt for f in self.findings_for(art, sources=[said])
                          if f.check_id == "grounding.number_supported"], ["3"])

    def test_an_identifier_is_not_a_number(self):
        art = artifact(statement="When a topic receives the third report, the system shall create one item in the review queue, as R2 and v1 say.")
        self.assertNotIn("grounding.number_supported", check_ids(self.findings_for(art)))


class TestStatementSupportedLanguage(ItemCheckCase):
    """The language of the source is the language of what was said (16/11/2026)."""

    PT_TURN = "Preciso que o sistema avise o moderador uma vez por tópico, quando a terceira denúncia chegar."
    EN_KNOWLEDGE = "The moderator of the forum wants to be told once per topic when the third report of the topic arrives. " * 3

    def test_an_expert_file_in_the_other_language_does_not_set_the_language(self):
        sources = [source("T2", text=self.PT_TURN), source("expert:forum", text=self.EN_KNOWLEDGE, kind="expert")]
        art = artifact(statement="O sistema deve exportar o relatório mensal para o gerente da loja.", evidences=[evidence("T2"), evidence("expert:forum")])
        [finding] = [f for f in self.findings_for(art, sources=sources) if f.check_id == "grounding.statement_supported"]
        self.assertIn("not supported", finding.message)
        self.assertNotIn("is in Portuguese and its source", finding.message)

    def test_a_statement_in_another_language_than_the_turn_says_so(self):
        sources = [source("T2", text=self.PT_TURN), source("expert:forum", text=self.EN_KNOWLEDGE, kind="expert")]
        art = artifact(statement="The system shall export the monthly report of the store to the manager of the store.",
                       evidences=[evidence("T2"), evidence("expert:forum")])
        [finding] = [f for f in self.findings_for(art, sources=sources) if f.check_id == "grounding.statement_supported"]
        self.assertIn("is in English and its source", finding.message)


class TestGroundingWithAQuote(ItemCheckCase):
    """An artefact that quotes its source: the quote must be in the source."""

    def test_a_true_quote_does_not_vouch_for_a_statement_that_shares_nothing_with_the_source(self):
        # 16/11/2026: the quote used to switch statement_supported off, so an invented requirement
        # with a real quote passed the correctness competency.
        art = user_story(statement="O gerente exporta o relatório mensal em planilha para a contabilidade.", evidences=[quote()])
        findings = self.findings_for(art)
        self.assertNotIn("grounding.quote_supported", check_ids(findings))
        self.assertEqual(self.severities(findings, "grounding.statement_supported"), [Severity.MAJOR])

    def test_no_source(self):
        self.assertIn("grounding.evidence_present", check_ids(self.findings_for(user_story(evidences=[]))))

    def test_a_missing_source_is_blocking(self):
        findings = self.findings_for(user_story(evidences=[quote(source_id="NEED-999")]))
        self.assertEqual(self.severities(findings, "grounding.evidence_resolvable"), [Severity.BLOCKER])

    def test_an_invented_quote_is_blocking(self):
        findings = self.findings_for(user_story(evidences=[quote(text="o cliente pediu envio de SMS a cada denúncia")]))
        self.assertEqual(self.severities(findings, "grounding.quote_supported"), [Severity.BLOCKER])

    def test_a_literal_quote_passes(self):
        self.assertNotIn("grounding.quote_supported", check_ids(self.findings_for(user_story())))

    def test_a_quote_with_other_accents_and_case_passes(self):
        art = user_story(evidences=[quote(text="O TOPICO ENTRE NUMA FILA DE REVISAO")])
        self.assertNotIn("grounding.quote_supported", check_ids(self.findings_for(art)))

    def test_a_quote_without_a_reason_is_only_informative(self):
        findings = self.findings_for(user_story(evidences=[quote(reason="")]))
        self.assertEqual(self.severities(findings, "grounding.reason_present"), [Severity.INFO])


class TestGroundingWithoutAQuote(ItemCheckCase):
    """An artefact that cites a turn without quoting it: the statement must be supported by the turn."""

    def test_a_statement_supported_by_its_turn_passes(self):
        self.assertNotIn("grounding.statement_supported", check_ids(self.findings_for(artifact())))

    def test_a_statement_the_turn_does_not_support(self):
        art = artifact(statement="When a payment is declined, the system shall email the finance team.")
        findings = self.findings_for(art)
        self.assertEqual(self.severities(findings, "grounding.statement_supported"), [Severity.MAJOR])

    def test_a_cited_turn_that_does_not_exist_is_blocking(self):
        findings = self.findings_for(artifact(evidences=[evidence(source_id="T99")]))
        self.assertEqual(self.severities(findings, "grounding.evidence_resolvable"), [Severity.BLOCKER])
        self.assertNotIn("grounding.statement_supported", check_ids(findings))  # nothing to compare with

    def test_citing_without_a_quote_is_not_a_finding_in_itself(self):
        ids = check_ids(self.findings_for(artifact()))
        self.assertNotIn("grounding.quote_supported", ids)
        self.assertNotIn("grounding.reason_present", ids)

    def test_the_threshold_is_configurable(self):
        from gigabrain.validation.registry import CheckConfig
        strict = CheckConfig(min_statement_support=0.95)
        self.assertIn("grounding.statement_supported", check_ids(self.findings_for(artifact(), config=strict)))


class _Repository:
    """A traceability provider that knows a fixed set of requirements."""

    def __init__(self, *known):
        self.known = set(known)

    def knows(self, requirement_id):
        return requirement_id in self.known

    def predecessors(self, requirement_id):
        return []

    def impacted_by(self, requirement_id):
        return []


class TestLinks(ItemCheckCase):
    def test_link_to_a_requirement_of_the_run(self):
        art = artifact("R2", links=[LinkRef("refines", "R1")])
        self.assertNotIn("grounding.links_resolvable", check_ids(self.findings_for(art, artifact("R1"))))

    def test_link_to_a_requirement_the_repository_knows(self):
        art = artifact(links=[LinkRef("supersedes", "R7")])
        self.assertNotIn("grounding.links_resolvable", check_ids(self.findings_for(art, trace=_Repository("R7"))))

    def test_link_to_a_requirement_that_does_not_exist(self):
        art = artifact(links=[LinkRef("refines", "R99")])
        findings = self.findings_for(art, trace=_Repository("R7"))
        self.assertEqual(self.severities(findings, "grounding.links_resolvable"), [Severity.MAJOR])

    def test_new_version_of_a_requirement_that_does_not_exist(self):
        art = artifact(links=[LinkRef("base", "R99")])
        findings = self.findings_for(art, trace=_Repository())
        self.assertEqual(self.severities(findings, "grounding.links_resolvable"), [Severity.MAJOR])

    def test_without_a_provider_it_says_it_could_not_check(self):
        # "I do not know" is not "it does not exist".
        findings = self.findings_for(artifact(links=[LinkRef("refines", "R99")]))
        self.assertEqual(self.severities(findings, "grounding.links_resolvable"), [Severity.INFO])

    def test_unknown_link_kind(self):
        art = artifact("R2", links=[LinkRef("improves", "R1")])
        findings = self.findings_for(art, artifact("R1"))
        self.assertEqual(self.severities(findings, "grounding.links_resolvable"), [Severity.MINOR])

    def test_link_to_itself(self):
        findings = self.findings_for(artifact("R1", links=[LinkRef("refines", "R1")]))
        self.assertEqual(self.severities(findings, "grounding.links_resolvable"), [Severity.MAJOR])

    def test_link_to_another_proposed_requirement_by_title(self):
        # Regression, 20/10/2026: P2 depends on P1, both proposed together and written by title.
        # The link is real; returning it as missing made the Twin delete it.
        p1 = artifact("P1", title="Registrar reclamação de loja")
        p2 = artifact("P2", title="Colocar loja em atenção", links=[LinkRef("depends_on", "Registrar reclamação de loja")])
        findings = self.findings_for(p2, p1, trace=_Repository("R7"))
        self.assertEqual(self.severities(findings, "grounding.links_resolvable"), [Severity.INFO])

    def test_link_to_another_proposed_requirement_by_id(self):
        findings = self.findings_for(artifact("P2", links=[LinkRef("depends_on", "P1")]), artifact("P1"), trace=_Repository("R7"))
        self.assertEqual(self.severities(findings, "grounding.links_resolvable"), [Severity.INFO])

    def test_link_to_its_own_title(self):
        art = artifact("P1", title="Registrar reclamação de loja", links=[LinkRef("refines", "Registrar reclamação de loja")])
        findings = self.findings_for(art, trace=_Repository("R7"))
        self.assertEqual(self.severities(findings, "grounding.links_resolvable"), [Severity.MAJOR])

    def test_duplicate_claim_that_holds(self):
        art = artifact("R2", links=[LinkRef("duplicates", "R1")])
        self.assertNotIn("grounding.duplicate_claim", check_ids(self.findings_for(art, artifact("R1"))))

    def test_duplicate_claim_that_does_not_hold(self):
        other = artifact("R1", statement="When a payment is declined, the system shall email the finance team.")
        art = artifact("R2", links=[LinkRef("duplicates", "R1")])
        self.assertEqual(self.severities(self.findings_for(art, other), "grounding.duplicate_claim"), [Severity.MAJOR])


class TestUnderstandability(ItemCheckCase):
    def test_long_sentence(self):
        long_statement = (
            "When the forum moderator opens the review queue and picks any item from the list of topics that "
            "were reported by the users of the system at any moment of the day or of the night, the system "
            "shall create one item."
        )
        self.assertIn("readability.sentence_length", check_ids(self.findings_for(artifact(statement=long_statement))))

    def test_a_gherkin_criterion_is_not_measured_as_one_sentence(self):
        long_criterion = Criterion(
            "Third report", "a topic with two reports from two different users of the forum that are still open",
            "a third user who is not one of the first two reports the same topic again",
            "the system creates one item for the topic in the review queue")
        ids = check_ids(self.findings_for(artifact(criteria=[long_criterion, CLEAN_CRITERIA[1]])))
        self.assertNotIn("readability.sentence_length", ids)

    def test_compound_requirement(self):
        art = artifact(statement="When a topic receives the third report, the system shall create one item and also export a report.")
        self.assertIn("readability.atomicity", check_ids(self.findings_for(art)))

    def test_glossary_term_without_a_definition(self):
        from gigabrain.validation.model import GlossaryEntry
        findings = self.findings_for(artifact(), glossary=[GlossaryEntry("review queue", "")])
        self.assertEqual(self.severities(findings, "readability.glossary_usage"), [Severity.INFO])


class TestSourcesAbsent(ItemCheckCase):
    """Without sources in the run, grounding is not assessed: 'I do not know', not 'it does not exist'."""

    def test_no_grounding_finding_against_missing_material(self):
        findings = self.findings_for(artifact(), sources=[])
        self.assertFalse({"grounding.evidence_resolvable", "grounding.statement_supported"} & check_ids(findings))

    def test_with_sources_a_wrong_citation_is_still_blocking(self):
        findings = self.findings_for(artifact(evidences=[evidence(source_id="T99")]), sources=[source()])
        self.assertIn("grounding.evidence_resolvable", check_ids(findings))


if __name__ == "__main__":
    unittest.main()
