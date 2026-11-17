"""Verifiability checks (dimension VERIFIABLE).

The question is always the same: reading this criterion, would someone know how
to write the test that decides whether it passed? The Digital Twin prompt
already states the ruler ("every 'then' must be verifiable: no fast, easy,
user-friendly without a number") and these checks apply it from outside.

A criterion counts as observable when it brings (a) an observable effect of the
system, (b) a measurable quantity or (c) a literal expected value; a plain-text
criterion may also bring (d) a conditional structure. In a Gherkin criterion the
structure is given, so what is looked at is the Then clause: "then the system
behaves as described" has the form of a criterion and tests nothing.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..lexicons import CONDITIONAL_OPENERS, OBSERVABLE_RE, STOPWORDS
from ..model import Artifact, ArtifactKind, Dimension, Finding, Level, Severity
from ..registry import CheckContext, item_check
from ..text import has_measurable_quantity, jaccard, normalize, word_count
from ._sources import inherited_vagueness, said_in_sources
from .ambiguity import vague_hits

#: A Then clause this close to the statement only repeats it.
RESTATEMENT_SIMILARITY = 0.90


def _has_conditional_structure(text: str) -> bool:
    """The criterion opens with a condition and has the comma between trigger and effect.

    Asking for the position is not fussiness: without it, "the queue shall be fast
    if possible" would pass for a conditional just because it has the word "if".
    """
    canon = normalize(text)
    if "," not in (text or ""):
        return False
    tokens = canon.split(" ")
    return any(" ".join(tokens[:n]) in CONDITIONAL_OPENERS for n in (1, 2, 3))


#: Phrases that look like an effect and say nothing ("the system behaves as follows").
#: Removed before looking for a verb of effect, since "follow" and "require" are verbs of
#: effect elsewhere ("the page follows the OUS standards").
_FILLER_RE = re.compile(r"\b(as|conforme|como) (follows|expected|described|specified|intended|required|"
                        r"esperado|descrito|especificado|previsto)\b")


#: An action stated by the grammar, for the verbs no word list will ever have ("the messages are
#: transported", "the system does not execute", "the system coordinates"). What a state or an
#: expectation says in the same form is not an action ("is considered", "is satisfied").
_NOT_AN_ACTION = "expected required intended described considered satisfied needed desired supposed based related given"
_STATED_ACTION_RE = re.compile(
    rf"\b(?:is|are|be|been|being)\s+(?:not\s+|no longer\s+)?(?!(?:{_NOT_AN_ACTION.replace(' ', '|')})\b)\w+(?:ed|en)\b"
    r"|\b(?:does|do|did)\s+not\s+\w+"
    r"|\b(?:system|site|website|application|app|server|service|platform|software|tool|api|page|module|component|solution|program|interface|database)\s+"
    r"(?:still\s+|also\s+|then\s+|only\s+)?(?!(?:behaves|functions|operates|acts|is|has|does|was|needs|wants)\b)\w+s\b"
)


#: The same in Portuguese (16/11/2026). In the real conversations the Twin wrote "o sistema termina
#: com código de saída 1", "o sistema encerra sem executar", "é utilizada", "não oferece", and the
#: lexicon had none of these verbs. Three forms: passive voice ("é exibido", "são listados", "fica
#: registado"), a negated verb ("não altera"), and a subject of the system type followed by a verb in
#: the third person ("o sistema termina"). The endings are those of the present and of the preterite;
#: the words that take the same position without being an action (adverbs, determiners, the state and
#: auxiliary verbs) are listed, as are the participles that state an expectation ("é suportado",
#: "é considerado", "é declarado"), which are the vague ones.
_PT_SUBJECTS = ("sistema|site|aplicacao|app|servidor|servico|plataforma|software|ferramenta|api|pagina|"
                "modulo|componente|solucao|programa|interface|banco")
_PT_NOT_AN_ACTION = ("considerad|esperad|previst|descrit|especificad|exigid|requerid|suportad|declarad|garantid|"
                     "atendid|basead|relacionad|definid|desejad|pretendid|dad")
_PT_NOT_A_VERB = ("esta|estao|era|foi|fica|ficam|tem|possui|pode|deve|precisa|necessita|funciona|opera|comporta|"
                  "atua|garante|assegura|parece|que|essa|esse|este|nesse|nessa|neste|nesta|dele|dela|deste|desta|"
                  "sempre|nunca|ainda|apenas|somente|tambem|agora|entao|como|conforme|segundo|sobre|entre|para|"
                  "desde|durante|antes|cada|toda|mesma|outra|alguma|nenhuma")
_PT_VERB = rf"(?!(?:{_PT_NOT_A_VERB})\b)\w{{3,}}(?:a|e|am|em|ou|iu|eu)\b"
_PT_STATED_ACTION_RE = re.compile(
    rf"\b(?:nao\s+|nunca\s+)?(?:e|sao|esta|estao|foi|foram|fica|ficam|sera|serao)\s+(?!(?:{_PT_NOT_AN_ACTION})[oa]s?\b)\w+(?:ad|id)[oa]s?\b"
    rf"|\b(?:nao|nunca|jamais)\s+{_PT_VERB}"
    rf"|\b[oa]\s+(?:{_PT_SUBJECTS})\s+(?:nao\s+|nunca\s+)?(?:ainda\s+|tambem\s+|so\s+|apenas\s+|entao\s+|sempre\s+)?{_PT_VERB}"
)

#: A literal expected value (16/11/2026): the test compares what it sees with the value, so the
#: criterion closes whatever the verb. A quoted text ('a linha 1 é "Ana"'), or a number written with
#: digits that is not a reference to a part of a document ("exit code 0", "line 1", "HTTP 404"; not
#: "section 3.2", "step 2", "version 1.1"). A number with a unit is already a measurable quantity.
_QUOTED_RE = re.compile(r"[\"“”«][^\"“”«»]{1,120}[\"“”»]|(?<!\w)['‘][^'‘’]{1,120}['’](?!\w)")
_REFERENCE_NOUNS = frozenset(
    "section secao item step passo version versao chapter capitulo appendix apendice anexo figure figura "
    "table tabela page pagina requirement requisito clause clausula paragraph paragrafo part parte note nota "
    "phase fase sprint release".split()
)
_NUMBER_RE = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?(?![\w.,])")


def _has_literal_value(text: str) -> bool:
    if _QUOTED_RE.search(text or ""):
        return True
    canon = normalize(text, keep_punct=True)
    for match in _NUMBER_RE.finditer(canon):
        before = canon[:match.start()].split()
        if not before or before[-1].strip(".,;:()") not in _REFERENCE_NOUNS:
            return True
    return False


def _has_effect(text: str) -> bool:
    plain = _FILLER_RE.sub(" ", normalize(text))
    stated = OBSERVABLE_RE.search(plain) or _STATED_ACTION_RE.search(plain) or _PT_STATED_ACTION_RE.search(plain)
    return bool(stated) or has_measurable_quantity(text) or _has_literal_value(text)


def _then_clauses(artifact: Artifact) -> list[tuple[int, str, str]]:
    """(index, clause to judge, full text) of each criterion.

    For a Gherkin criterion the clause is its Then; for plain text, the whole criterion.
    """
    out = []
    for index, text in enumerate(artifact.acceptance_criteria, start=1):
        if not text.strip():
            continue
        structured = artifact.criteria[index - 1] if index <= len(artifact.criteria) else None
        clause = structured.then if structured is not None and structured.then.strip() else text
        out.append((index, clause, text))
    return out


@item_check(
    id="verifiability.criterion_observable",
    dimension=Dimension.VERIFIABLE,
    severity=Severity.MAJOR,
    description=(
        "Each acceptance criterion describes something observable: an effect of the system, "
        "a measurable quantity or a literal expected value (for a Gherkin criterion, in its Then clause)."
    ),
    weight=1.5,
)
def criterion_observable(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    for index, clause, text in _then_clauses(artifact):
        structured = index <= len(artifact.criteria)
        if _has_effect(clause) or (not structured and _has_conditional_structure(text)):
            continue
        yield Finding(
            check_id="verifiability.criterion_observable",
            dimension=Dimension.VERIFIABLE,
            severity=Severity.MAJOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator=f"acceptance_criteria[{index}]" + (".then" if structured else ""),
            excerpt=clause.strip()[:160],
            message="The acceptance criterion states no observable effect and no quantity.",
            suggestion=(
                "Say what can be seen when it passes: 'then the <system> <observable effect>', "
                "or add a value with a unit."
            ),
        )


@item_check(
    id="verifiability.criterion_not_vague",
    dimension=Dimension.VERIFIABLE,
    severity=Severity.MAJOR,
    description="No acceptance criterion relies on a subjective term without a number.",
    weight=1.2,
)
def criterion_not_vague(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    for index, text in enumerate(artifact.acceptance_criteria, start=1):
        text = text.strip()
        if not text:
            continue
        for term, _excerpt in vague_hits(text):
            if said_in_sources(term, artifact, ctx):  # inherited: a gap of the source
                message, suggestion = inherited_vagueness(term)
                yield Finding(check_id="verifiability.criterion_not_vague", dimension=Dimension.VERIFIABLE,
                              severity=Severity.INFO, level=Level.ITEM, target_id=artifact.id,
                              locator=f"acceptance_criteria[{index}]", excerpt=text[:160],
                              message=message, suggestion=suggestion)
                continue
            yield Finding(
                check_id="verifiability.criterion_not_vague",
                dimension=Dimension.VERIFIABLE,
                severity=Severity.MAJOR,
                level=Level.ITEM,
                target_id=artifact.id,
                locator=f"acceptance_criteria[{index}]",
                excerpt=text[:160],
                message=f"The acceptance criterion relies on a subjective term: '{term}'.",
                suggestion=(
                    "A criterion with a subjective term does not close: two reviewers reach "
                    "different answers about the same result."
                ),
            )


@item_check(
    id="verifiability.criterion_restates",
    dimension=Dimension.VERIFIABLE,
    severity=Severity.MINOR,
    description="The Then clause of a criterion says more than the statement repeated.",
    requires="Applies to artefacts whose criteria come in Gherkin form.",
)
def criterion_restates(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    statement = artifact.statement.strip()
    if not statement:
        return
    for index, criterion in enumerate(artifact.criteria, start=1):
        then = criterion.then.strip()
        if then and jaccard(then, statement, stopwords=STOPWORDS) >= RESTATEMENT_SIMILARITY:
            yield Finding(
                check_id="verifiability.criterion_restates",
                dimension=Dimension.VERIFIABLE,
                severity=Severity.MINOR,
                level=Level.ITEM,
                target_id=artifact.id,
                locator=f"acceptance_criteria[{index}].then",
                excerpt=then[:160],
                message="The Then clause only repeats the statement.",
                suggestion="A criterion is a concrete case of the requirement: give the input and the result expected for it.",
            )


@item_check(
    id="verifiability.criterion_length",
    dimension=Dimension.VERIFIABLE,
    severity=Severity.MINOR,
    description="Acceptance criteria are short enough to test one behaviour at a time.",
)
def criterion_length(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    limit = ctx.config.max_words_per_criterion
    for index, criterion in enumerate(artifact.acceptance_criteria, start=1):
        count = word_count(criterion)
        if count > limit:
            yield Finding(
                check_id="verifiability.criterion_length",
                dimension=Dimension.VERIFIABLE,
                severity=Severity.MINOR,
                level=Level.ITEM,
                target_id=artifact.id,
                locator=f"acceptance_criteria[{index}]",
                excerpt=criterion.strip()[:160],
                message=f"Criterion with {count} words (limit: {limit}).",
                suggestion="Split it into smaller criteria, one behaviour each.",
            )


@item_check(
    id="verifiability.enabler_has_criteria",
    dimension=Dimension.VERIFIABLE,
    severity=Severity.MINOR,
    description="A technical enabler also has an acceptance criterion; otherwise there is no telling when it is done.",
    requires="Applies to artefacts of kind technical_enabler.",
)
def enabler_has_criteria(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    if artifact.kind is not ArtifactKind.TECHNICAL_ENABLER:
        return
    if not [c for c in artifact.acceptance_criteria if c.strip()]:
        yield Finding(
            check_id="verifiability.enabler_has_criteria",
            dimension=Dimension.VERIFIABLE,
            severity=Severity.MINOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="acceptance_criteria",
            message="The enabler has no acceptance criterion.",
            suggestion="Define the observable state that makes the enabler done.",
        )
