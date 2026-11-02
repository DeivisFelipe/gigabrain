"""Lexicons used by the lexical checks (ambiguity, conciseness, verifiability...).

Where each list comes from matters for the paper, because a lexicon is a design
choice and not a natural fact:

- (T) terms named in the Digital Twin prompts: the current one asks the agent to
  point out "vague words without a criterion (fast, intuitive)" and forbids
  "fast, easy, user-friendly without a number"; the first prototype named
  "melhor / rápido / intuitivo / funcionar bem".
- (R) terms the ReqInOne paper reports as subjective wording in GPT-4o output
  ("appropriate", "user-friendly").
- VAGUE_TERMS_HOST is the list of gigabrain/requirement_format.py, imported and
  not copied: whatever the format check of the project calls vague, validation
  calls vague too.
- The rest is our extension, by direct analogy. It lives in its own constants so
  that the ablation in the experiment can switch each origin on and off.

Maintenance rule: a new term goes here, never inside a check. English and
Portuguese terms sit side by side because the PO may talk in either language.
"""

from __future__ import annotations

import re

from ..evaluation import STOPWORDS as _HOST_STOPWORDS
from ..requirement_format import VAGUE_TERMS as _HOST_VAGUE_TERMS
from .text import normalize

# --------------------------------------------------------------------------
# Ambiguity: adjectives and adverbs without a measurable criterion
# --------------------------------------------------------------------------

#: Terms named in the project's own artefacts.
VAGUE_TERMS_SEED: tuple[str, ...] = (
    "fast",           # (T)
    "intuitive",      # (T)
    "easy",           # (T)
    "user-friendly",  # (T) (R)
    "appropriate",    # (R)
    "melhor",         # (T) first prototype
    "rápido",         # (T) first prototype
    "intuitivo",      # (T) first prototype
    "funcionar bem",  # (T) first prototype: "deve funcionar bem" was its counter-example
)

#: The list the project's format check already uses.
VAGUE_TERMS_HOST: tuple[str, ...] = tuple(_HOST_VAGUE_TERMS)

#: Our extension, by analogy with the ones above.
VAGUE_TERMS_EXTENDED: tuple[str, ...] = (
    "friendly", "seamless", "state-of-the-art", "easy to use", "work well", "works well",
    "good experience", "reasonable", "sufficient", "acceptable", "satisfactory", "significant",
    "relevant", "modern", "attractive", "optimized", "lightweight", "scalable", "effective",
    "comfortable", "pleasant", "in a timely manner", "transparently", "minimal impact", "high availability",
    "eficaz", "escalável", "confortável", "agradável", "moderno", "atraente", "otimizado",
    "performático", "leve", "ágil", "satisfatório", "aceitável", "razoável", "suficiente",
    "significativo", "relevante", "considerável", "mínimo impacto", "alta disponibilidade",
    "boa experiência", "sempre que necessário", "o mais breve possível", "em tempo hábil",
    "de forma transparente", "sem impacto",
)


def _unique(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """Join the groups keeping the first spelling of each term (accents ignored)."""
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for term in group:
            key = normalize(term)
            if key and key not in seen:
                seen.add(key)
                out.append(term)
    return tuple(out)


VAGUE_TERMS: tuple[str, ...] = _unique(VAGUE_TERMS_SEED, VAGUE_TERMS_HOST, VAGUE_TERMS_EXTENDED)

#: Words that make the requirement optional and therefore impossible to verify.
OPTIONALITY_TERMS: tuple[str, ...] = (
    "if possible", "when possible", "where possible", "if needed", "if necessary", "where applicable",
    "if applicable", "ideally", "preferably", "should preferably", "maybe", "perhaps", "eventually",
    "it would be nice", "would be nice", "nice to have",
    "se possível", "quando possível", "se necessário", "quando aplicável", "se aplicável",
    "idealmente", "preferencialmente", "talvez", "eventualmente", "pode ser que", "poderia",
    "deveria", "seria interessante", "seria bom",
)

#: Quantifiers without a number. They only become a finding when no quantity is near.
UNQUANTIFIED_TERMS: tuple[str, ...] = (
    "many", "few", "several", "large", "small", "huge", "frequently", "rarely",
    "periodically", "regularly", "often", "usually", "normally", "in general",
    "muitos", "muitas", "poucos", "poucas", "vários", "várias", "diversos", "diversas", "alguns",
    "algumas", "grande", "pequeno", "enorme", "mínimo", "máximo", "baixo", "alto", "curto", "longo",
    "frequentemente", "raramente", "periodicamente", "regularmente", "em geral", "normalmente",
)

#: Absolute generalisations: almost always false and always hard to test.
OVERGENERALIZATION_TERMS: tuple[str, ...] = (
    "always", "never", "all cases", "every case", "any situation", "fully", "completely", "totally",
    "100% of the cases", "under no circumstances",
    "sempre", "nunca", "todos os casos", "qualquer situação", "totalmente", "completamente",
    "100% dos casos", "em hipótese alguma",
)

# --------------------------------------------------------------------------
# Conciseness: filler and beating about the bush
# --------------------------------------------------------------------------

FILLER_TERMS: tuple[str, ...] = (
    "basically", "simply", "obviously", "in fact", "it is worth noting",
    "it is important to note", "it should be noted", "in general terms", "so to speak",
    "among other things", "and so on", "and so forth", "with regard to", "with respect to",
    "as a matter of fact", "needless to say",
    "basicamente", "simplesmente", "obviamente", "claramente", "na verdade", "de fato",
    "vale ressaltar", "vale lembrar", "é importante destacar", "cabe salientar", "de modo geral",
    "de certa forma", "por assim dizer", "entre outras coisas", "e assim por diante",
    "dentre outros", "no que diz respeito a", "com relação a", "no sentido de",
)

# --------------------------------------------------------------------------
# Verifiability: signs of an observable criterion
# --------------------------------------------------------------------------

#: Words that open a conditional structure. They only count at the START of the
#: criterion and with a comma after: "if possible" in the middle of a sentence makes
#: nothing observable, and that false negative is what led to the positional rule
#: (see verifiability._has_conditional_structure).
CONDITIONAL_OPENERS: tuple[str, ...] = (
    "given", "when", "if", "while", "where", "once", "after", "before", "whenever", "upon",
    "as soon as", "every time",
    "quando", "se", "caso", "dado", "dados", "ao", "apos", "assim que", "sempre que", "enquanto",
    "toda vez que",
)

# Verbs that describe an observable effect of the system. The list is long on
# purpose: the check it feeds wants to find the criterion with NO concrete effect
# ("the system behaves as described", "works well", "the need is met"), not to grade
# how good a concrete one is. Since a failed check sends the artefact back to the
# agent, a false alarm costs more than a miss.
_EN_VERBS = (
    "show display send return log block receive create update delete remove store save reject accept "
    "redirect export import print queue email record generate open close enable disable list appear "
    "contain include calculate compute start stop load download upload lock unlock archive restore "
    "validate confirm prompt ask warn alert inform report present render highlight sort filter search "
    "retrieve fetch add select assign mark flag cancel approve grant revoke expire encrypt decrypt "
    "authenticate authorize play pause sync schedule publish hide clear reset increment decrement count "
    "limit allow prevent require refuse fail succeed complete finish trigger emit post submit change "
    "keep remain stay become issue deliver produce reach exceed equal match notify deny apply copy retry "
    # Added on 20/10/2026: in the meetings processed with DeepSeek, 87 Then clauses such as
    # "the system indicates which colleges are missing" or "the site passes its test suite in
    # Firefox" were rejected as having no effect. Nouns that are also verbs (work, state, test,
    # review, link, run, use) enter only inflected, in _EN_IRREGULAR, so "the review" or "the use
    # of" stay nouns; describe and determine enter only as "describes" and "determines", so the
    # filler "behaves as the PO described" still has no effect.
    "indicate respond conform comply exhibit follow represent identify adjust provide navigate pass meet "
    "implement reflect"
).split()
#: Irregular forms and doubled consonants the regular endings do not produce.
_EN_IRREGULAR = (
    "sent shown kept written read found lost set became begun got "
    "logged logging flagged flagging stopped stopping submitted submitting emitted emitting synced "
    "resetting setting works worked stated tested reviewed linked runs ran uses used describes determines"
).split()


def _en_verb_pattern(verb: str) -> str:
    """Base form plus the regular endings (-s, -ed, -ing), minding a final "e" or "y"."""
    if verb.endswith("e"):
        return re.escape(verb[:-1]) + r"(?:e|es|ed|ing)"
    if verb.endswith("y") and verb[-2] not in "aeiou":
        return re.escape(verb[:-1]) + r"(?:y|ies|ied|ying)"
    if verb.endswith(("s", "x", "ch", "sh")):
        return re.escape(verb) + r"(?:es|ed|ing)?"
    return re.escape(verb) + r"(?:s|ed|ing)?"


# Portuguese verbs are stems plus the endings of their conjugation. Listing the
# endings (instead of accepting anything after the stem) keeps "important",
# "information" and "geral" from counting as "importar", "informar" and "gerar".
_PT_AR_STEMS = (
    "mostr envi registr retorn grav notific rejeit aceit atualiz calcul redirecion export "
    "import salv public apresent armazen list ger fech habilit desabilit valid confirm alert inform "
    "orden filtr busc recuper adicion selecion cancel aprov expir criptograf autentic autoriz agend "
    "limit dispar cri "
    # Added on 20/10/2026, after the first run with a real LLM wrote "o sistema nega o acesso".
    # Left out on purpose because the bare form is also a common noun: conta, marca,
    # entrada, passa(do), soma.
    "neg const fic ativ desativ reativ sinaliz ocult apag recus increment decrement zer "
    "contabiliz carreg associ vincul destac solicit encaminh"
).split()
_PT_AR_ENDINGS = r"(?:a|am|ar|ou|aram|ado|ada|ados|adas|ando|ara|arao|e|em)"
#: Verbs in -ear take an "i" in the present: bloqueia, bloqueiam.
_PT_EAR_STEMS = "bloque desbloque rastre".split()
_PT_EAR_ENDINGS = r"(?:ia|iam|ar|ou|aram|ado|ada|ados|adas|ando|e|em)"
_PT_ER_IR_STEMS = (
    "exib receb remov inclu exclu imprim permit imped exig emit ped abr "
    # Added on 20/10/2026: "a loja aparece na listagem" was not observable.
    "aparec desaparec permanec devolv respond substitu atribu"
).split()
_PT_ER_IR_ENDINGS = r"(?:e|em|er|ir|eu|iu|i|ido|ida|idos|idas|endo|indo|a|am)"
_PT_IRREGULAR = "mantem manter mantido mantida contem conter contido contida sai saem sair saiu".split()

OBSERVABLE_RE = re.compile(
    r"(?<!\w)(?:"
    + "|".join(_en_verb_pattern(v) for v in _EN_VERBS)
    + "|" + "|".join(_EN_IRREGULAR + _PT_IRREGULAR)
    + r"|(?:" + "|".join(_PT_AR_STEMS) + ")" + _PT_AR_ENDINGS
    + r"|(?:" + "|".join(_PT_EAR_STEMS) + ")" + _PT_EAR_ENDINGS
    + r"|(?:" + "|".join(_PT_ER_IR_STEMS) + ")" + _PT_ER_IR_ENDINGS
    + r")(?!\w)"
)

# --------------------------------------------------------------------------
# Atomicity: signs of more than one requirement in the same sentence
# --------------------------------------------------------------------------

CONJUNCTION_TERMS: tuple[str, ...] = (
    "and also", "as well as", "in addition", "and/or", "along with", "and furthermore",
    "e também", "além disso", "bem como", "assim como", "e ainda", "e, além", "e/ou", "ou então",
    "juntamente com",
)

# --------------------------------------------------------------------------
# Consistency: negation
# --------------------------------------------------------------------------

#: A statement that carries one of these says the opposite of the same statement without it.
NEGATION_RE = re.compile(
    r"(?<!\w)(not|never|no longer|cannot|cant|without|prevent\w*|forbid\w*|prohibit\w*|disallow\w*|"
    r"nao|jamais|nunca|sem que|imped\w*|proib\w*|bloque\w*)(?!\w)"
)

# --------------------------------------------------------------------------
# Stopwords for similarity and coverage
# --------------------------------------------------------------------------

_STOPWORDS_PT: frozenset[str] = frozenset(
    """
    a o as os um uma uns umas de do da dos das em no na nos nas por para
    com sem sob sobre entre ate apos e ou mas que se ao aos
    eu tu ele ela vos eles elas meu minha seu sua dele dela
    este esta isso isto aquele aquela aquilo
    ser estar ter haver fazer poder dever ir vir deve devem devera
    sao foi era sera seja sendo sido tem tinha havia
    como quero quando entao nao sim ja mais menos muito pouco
    sistema usuario
    """.split()
)

#: English stopwords are the ones the project's own evaluation uses, so that the
#: support measured here is comparable with its "unsupported" metric.
STOPWORDS: frozenset[str] = _STOPWORDS_PT | frozenset(_HOST_STOPWORDS) | frozenset({"given", "user", "users", "want", "wants"})

# --------------------------------------------------------------------------
# Generic roles: a vague persona in the user story
# --------------------------------------------------------------------------

GENERIC_PERSONAS: tuple[str, ...] = (
    "user", "a user", "the user", "users", "end user", "person", "someone", "anyone", "everyone",
    "anybody", "stakeholder",
    "usuário", "pessoa", "cliente final", "alguém", "todos", "qualquer um",
)

#: Specific roles a source may name. ambiguity.generic_persona asks for a concrete
#: role only when a cited source names one of these: otherwise "user" is
#: all the source supports, and asking for more is asking the writer to invent.
#: Finite on purpose, like every lexicon here: a role outside the list is "I do not
#: know", and the check stays silent. Added on 20/10/2026 from the roles that came up
#: in the dataset meetings and in the conversations with DeepSeek.
ROLE_TERMS: tuple[str, ...] = (
    "administrator", "admin", "moderator", "customer", "client", "buyer", "seller", "vendor",
    "student", "teacher", "professor", "instructor", "manager", "editor", "author", "reviewer",
    "operator", "librarian", "doctor", "physician", "nurse", "patient", "receptionist",
    "landlord", "tenant", "owner", "employee", "staff", "driver", "passenger", "guest", "member",
    "subscriber", "visitor", "developer", "analyst", "auditor", "accountant", "supervisor",
    "coordinator", "technician", "attendant",
    "administrador", "moderador", "cliente", "comprador", "vendedor", "aluno", "estudante",
    "professor", "gerente", "gestor", "editor", "autor", "revisor", "operador", "bibliotecário",
    "médico", "enfermeiro", "paciente", "recepcionista", "recepção", "locador", "locatário",
    "proprietário", "dono", "funcionário", "colaborador", "motorista", "passageiro", "hóspede",
    "membro", "assinante", "visitante", "desenvolvedor", "analista", "auditor", "contador",
    "supervisor", "coordenador", "técnico", "atendente", "lojista",
)

# --------------------------------------------------------------------------
# NFR subtype indicators (ReqInOne / Cleland-Huang et al.)
# --------------------------------------------------------------------------

#: Hint words per subtype. Only a clue for a suspicious classification, never a
#: classifier.
NFR_INDICATORS: dict[str, tuple[str, ...]] = {
    "availability": ("available", "availability", "uptime", "downtime", "24/7", "disponível", "disponibilidade", "fora do ar"),
    "fault_tolerance": ("failure", "fault", "recover", "recovery", "fallback", "backup", "falha", "recuperação"),
    "legal": ("law", "legal", "license", "regulation", "compliance", "gdpr", "lgpd", "lei", "regulamentação", "norma"),
    "look_and_feel": ("layout", "appearance", "colour", "color", "font", "theme", "aparência", "cor", "tipografia", "tema"),
    "maintainability": ("maintain", "maintainability", "modular", "documented", "refactor", "manutenção", "manutenibilidade"),
    "operational": ("operation", "environment", "deployment", "deploy", "monitoring", "operação", "ambiente", "implantação"),
    "performance": ("performance", "response time", "latency", "throughput", "seconds", "desempenho", "tempo de resposta", "latência"),
    "portability": ("portability", "browser", "operating system", "platform", "portabilidade", "navegador", "multiplataforma"),
    "scalability": ("scalability", "scale", "load", "concurrent users", "escalabilidade", "carga", "usuários simultâneos"),
    "security": ("security", "authentication", "authorization", "encryption", "password", "token", "access control",
                 "segurança", "autenticação", "autorização", "criptografia", "senha"),
    "usability": ("usability", "accessibility", "learn", "clicks", "training", "usabilidade", "acessibilidade", "treinamento"),
    "safety": ("safety", "harm", "injury", "hazard", "data loss", "dano", "perda de dados"),
}
