"""Tests of the text utilities.

They are the base of everything: if normalisation or quote comparison is wrong,
every check above it is wrong too, and silently. The requirements may be in
English or in Portuguese, so both are exercised.
"""

from __future__ import annotations

import unittest

from gigabrain.validation.lexicons import STOPWORDS
from gigabrain.validation.text import (
    containment,
    coverage,
    find_terms,
    has_measurable_quantity,
    jaccard,
    light_stem,
    normalize,
    sentences,
    term_regex,
    word_count,
)


class TestNormalize(unittest.TestCase):
    def test_strips_accents_punctuation_and_case(self):
        self.assertEqual(normalize("Notificação  de Pagamento!"), "notificacao de pagamento")

    def test_keeps_punctuation_when_asked(self):
        self.assertEqual(normalize("As a PO, I want X.", keep_punct=True), "as a po, i want x.")

    def test_empty_text(self):
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize(None), "")


class TestSentences(unittest.TestCase):
    def test_splits_on_punctuation(self):
        self.assertEqual(len(sentences("First. Second! Third?")), 3)

    def test_line_break_is_a_boundary(self):
        # Without this, the title and the statement would become a single sentence
        # and the length measure would report a giant sentence where there is none.
        self.assertEqual(len(sentences("Title without a period\nStatement without a period")), 2)

    def test_counts_words(self):
        self.assertEqual(word_count("As a moderator, I want to group reports."), 8)


class TestTermRegex(unittest.TestCase):
    def test_tolerates_gender_and_number_in_portuguese(self):
        pattern = term_regex("rápido")
        for variant in ("rapido", "rapida", "rapidos", "rapidas"):
            self.assertRegex(variant, pattern)

    def test_tolerates_the_vel_plural(self):
        self.assertRegex("amigaveis", term_regex("amigável"))
        self.assertRegex("amigavel", term_regex("amigável"))

    def test_respects_the_word_boundary(self):
        self.assertIsNone(term_regex("leve").search("levemente"))
        self.assertIsNone(term_regex("fast").search("breakfast"))

    def test_multi_word_term(self):
        self.assertTrue(find_terms("it needs a good experience", ["good experience"]))


class TestFindTerms(unittest.TestCase):
    def test_finds_an_inflected_term(self):
        hits = find_terms("A fila deve ser rápida se possível", ["rápido", "se possível"])
        self.assertEqual({t for t, _ in hits}, {"rápido", "se possível"})

    def test_does_not_find_what_is_not_there(self):
        self.assertEqual(find_terms("clean and objective text", ["intuitive"]), [])

    def test_a_term_inside_a_longer_one_is_one_hit(self):
        terms = ["friendly", "user-friendly"]
        self.assertEqual([t for t, _ in find_terms("a user-friendly page", terms)], ["user-friendly"])
        # On its own somewhere else, the shorter term is still found.
        self.assertEqual([t for t, _ in find_terms("a user-friendly page with a friendly tone", terms)], ["friendly", "user-friendly"])


class TestContainment(unittest.TestCase):
    SOURCE = "O cliente quer adicionar notificação ao pagamento em tempo real."

    def test_a_literal_quote_is_one(self):
        self.assertEqual(containment("adicionar notificação ao pagamento", self.SOURCE), 1.0)

    def test_ignores_accents_and_case(self):
        self.assertEqual(containment("ADICIONAR NOTIFICACAO", self.SOURCE), 1.0)

    def test_an_invented_quote_stays_low(self):
        self.assertLess(containment("enviar SMS para o gerente financeiro", self.SOURCE), 0.5)

    def test_empty_source(self):
        self.assertEqual(containment("anything", ""), 0.0)

    def test_empty_quote(self):
        self.assertEqual(containment("", self.SOURCE), 0.0)


class TestCoverage(unittest.TestCase):
    """Support of a statement by a source that is cited without being quoted."""

    UTTERANCE = "Uh, I'd like the the system to export monthly reports to PDF for the finance team."

    def test_a_cleaned_up_statement_is_covered(self):
        statement = "The system shall export monthly reports to PDF for the finance team."
        self.assertEqual(coverage(statement, self.UTTERANCE, stopwords=STOPWORDS), 1.0)

    def test_an_invented_statement_is_not(self):
        statement = "The system shall encrypt every backup with AES-256."
        self.assertEqual(coverage(statement, self.UTTERANCE, stopwords=STOPWORDS), 0.0)

    def test_a_claim_with_only_stopwords_counts_as_covered(self):
        self.assertEqual(coverage("The system shall", self.UTTERANCE, stopwords=STOPWORDS), 1.0)

    def test_empty_source(self):
        self.assertEqual(coverage("export reports", "", stopwords=STOPWORDS), 0.0)


class TestSimilarity(unittest.TestCase):
    def test_a_plural_does_not_separate_equal_requirements(self):
        self.assertEqual(jaccard("moderador recebe notificação", "moderador recebe notificações"), 1.0)
        self.assertEqual(jaccard("the user exports a report", "the users export reports"), 1.0)

    def test_different_requirements_have_low_similarity(self):
        a = "The moderator groups the reports of a topic in the queue"
        b = "The editor schedules the publication of a post"
        self.assertLess(jaccard(a, b, stopwords=STOPWORDS), 0.3)

    def test_singular_and_plural_reduce_to_the_same_stem(self):
        pairs = [("notificacoes", "notificacao"), ("moderadores", "moderador"), ("principais", "principal"),
                 ("stores", "store"), ("databases", "database"), ("categories", "category"), ("users", "user")]
        for plural, singular in pairs:
            self.assertEqual(light_stem(plural), light_stem(singular), plural)

    def test_the_stemmer_leaves_invariant_words_alone(self):
        for word in ("status", "pais", "access", "address"):
            self.assertEqual(light_stem(word), word)


class TestQuantity(unittest.TestCase):
    def test_recognises_a_value_with_a_unit(self):
        for text in ("within 3 seconds", "em até 3 segundos", "95% of the requests", "at least 8 characters",
                     "in under two seconds"):
            self.assertTrue(has_measurable_quantity(text), text)

    def test_does_not_recognise_an_adjective(self):
        self.assertFalse(has_measurable_quantity("it must be fast"))
        self.assertFalse(has_measurable_quantity("deve ser rápido"))


if __name__ == "__main__":
    unittest.main()
