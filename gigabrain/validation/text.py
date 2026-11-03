"""Text utilities used by the deterministic checks.

Everything here is standard library on purpose: the deterministic tier has to
run without an API key, without network and without external dependencies, so
that the result is reproducible and the tests run on any machine.

The requirements may be written in English or in Portuguese (the PO may talk in
Portuguese), so every function here works for both without asking which one it is.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+")
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize(text: str, *, keep_punct: bool = False) -> str:
    """Canonical form for comparison: no accents, lower case, single spaces.

    Used in every lexical comparison so that "Notificação" and "notificacao"
    count as the same word.
    """
    out = strip_accents(text or "").lower()
    if not keep_punct:
        out = _PUNCT_RE.sub(" ", out)
    return _WS_RE.sub(" ", out).strip()


def words(text: str) -> list[str]:
    return _WORD_RE.findall(normalize(text))


def word_count(text: str) -> int:
    return len(words(text))


def sentences(text: str) -> list[str]:
    """Naive sentence split. Enough to measure length.

    A line break counts as a boundary: in the artefacts, the title, the story
    and the criteria come on separate lines and must not be added up into one
    giant sentence.
    """
    out: list[str] = []
    for line in (text or "").splitlines():
        for part in _SENTENCE_RE.split(line.strip()):
            if part.strip():
                out.append(part.strip())
    return out


# --------------------------------------------------------------------------
# Term search
# --------------------------------------------------------------------------


def _word_pattern(word: str) -> str:
    """Regex of one word that tolerates gender and number.

    Without this, a Portuguese lexicon written in the masculine singular
    ("rápido") does not find "rápida", "rápidos" or "rápidas", and a lexical
    check that catches one form out of four is useless in Portuguese. The
    tolerance is morphological and conservative: it only touches the ending.
    English terms just get an optional plural "s".
    """
    if word.endswith(("o", "a")) and len(word) > 2:
        return re.escape(word[:-1]) + r"[oa]s?"
    if word.endswith("el") and len(word) > 3:          # amigavel / amigaveis
        return re.escape(word[:-2]) + r"e(?:l|is)"
    if word.endswith("il") and len(word) > 3:          # facil / faceis
        return re.escape(word[:-2]) + r"(?:il|eis)"
    if word.endswith("s"):                             # already plural
        return re.escape(word)
    return re.escape(word) + r"s?"


def term_regex(term: str) -> re.Pattern[str]:
    """Compile a lexicon term (one or more words) into a tolerant regex."""
    parts = [_word_pattern(w) for w in normalize(term).split()]
    if not parts:
        return re.compile(r"(?!x)x")  # a pattern that never matches
    return re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)")


@lru_cache(maxsize=4096)
def _cached_regex(term: str) -> re.Pattern[str]:
    return term_regex(term)


def find_terms(text: str, terms: tuple[str, ...] | list[str]) -> list[tuple[str, str]]:
    """Look for terms (multi-word ones included) at word boundaries.

    Returns pairs (canonical lexicon term, text around the hit). The search runs
    on the normalised form and tolerates gender and number; the excerpt also
    comes from the normalised text, because the goal is to show the context, not
    to rebuild the original character by character.
    """
    hay = normalize(text)
    if not hay:
        return []
    found = [(term, m.start(), m.end()) for term in terms for m in _cached_regex(term).finditer(hay)]
    hits: list[tuple[str, str]] = []
    seen: set[str] = set()
    for term, start, end in found:
        if term in seen:
            continue
        if any(s <= start and end <= e and e - s > end - start for _, s, e in found):
            continue  # inside a longer hit ("friendly" in "user-friendly"): one hit, not two
        seen.add(term)
        hits.append((term, hay[max(0, start - 30):min(len(hay), end + 30)].strip()))
    return hits


def contains_any(text: str, terms: tuple[str, ...] | list[str]) -> bool:
    return bool(find_terms(text, terms))


def matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    hay = normalize(text)
    return any(p.search(hay) for p in patterns)


# --------------------------------------------------------------------------
# Similarity
# --------------------------------------------------------------------------


# Words that end in "s" without being a plural. A short list of frequent ones;
# getting this wrong costs little (the measure only has to be consistent), but
# keeping these out avoids silly collisions.
_INVARIANT_S = frozenset(
    {"status", "pais", "mais", "menos", "apos", "atras", "depois", "pois",
     "tres", "lapis", "atraves", "alias", "onus", "bonus", "virus",
     "access", "address", "process", "business", "class", "pass", "less", "always",
     "this", "its", "was", "has", "does", "across", "analysis", "basis", "success"}
)


def light_stem(word: str) -> str:
    """Plural reduction for Portuguese and English, conservative on purpose.

    Without it, "notificação" and "notificações", or "report" and "reports",
    would count as different terms and duplicate detection would miss the
    obvious cases. It is not a real stemmer (it does not touch verb inflection),
    only enough not to get the trivial wrong.

    The final "e" is dropped as well, which is what makes both languages agree:
    "stores" -> "store" -> "stor" matches "store" -> "stor", and "moderadores"
    -> "moderadore" -> "moderador" matches "moderador".
    """
    w = word
    if len(w) > 3 and w.endswith("s") and w not in _INVARIANT_S:
        if w.endswith(("oes", "aes")):       # notificacoes -> notificacao
            w = w[:-3] + "ao"
        elif w.endswith("ais"):              # principais -> principal
            w = w[:-3] + "al"
        elif w.endswith("eis"):              # papeis -> papel
            w = w[:-3] + "el"
        elif w.endswith("ies"):              # categories -> categori (see "y" below)
            w = w[:-3] + "i"
        else:
            w = w[:-1]
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]
    elif len(w) > 4 and w.endswith("y"):     # category -> categori
        w = w[:-1] + "i"
    return w


@lru_cache(maxsize=8192)
def _tokens(text: str, stem: bool) -> frozenset[str]:
    return frozenset((light_stem(w) if stem else w) for w in words(text) if len(w) > 1)


@lru_cache(maxsize=64)
def _stemmed(stopwords: frozenset[str]) -> frozenset[str]:
    return frozenset(light_stem(w) for w in stopwords) | stopwords


def token_set(
    text: str,
    *,
    stopwords: frozenset[str] = frozenset(),
    stem: bool = True,
) -> set[str]:
    """Content tokens of a text. Stopwords are compared before and after stemming."""
    tokens = _tokens(text or "", stem)
    if not stopwords:
        return set(tokens)
    blocked = _stemmed(stopwords) if stem else stopwords
    return {t for t in tokens if t not in blocked}


def jaccard(
    a: str,
    b: str,
    *,
    stopwords: frozenset[str] = frozenset(),
    stem: bool = True,
) -> float:
    """Token-set similarity. Stable and cheap: good for duplicates."""
    sa = token_set(a, stopwords=stopwords, stem=stem)
    sb = token_set(b, stopwords=stopwords, stem=stem)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


#: Common words only one of the two languages uses, to tell them apart (no word that exists in both: "a", "as", "do", "no").
_LANGUAGE_WORDS = {
    "English": frozenset("the and of to that is are for with when shall must this from by which".split()),
    "Portuguese": frozenset("de da dos das que para com em uma deve quando nao ao aos pelo pela sao".split()),
}


def language_of(text: str) -> str | None:
    """"English", "Portuguese" or None when the text does not say clearly (too short, or mixed)."""
    found = words(strip_accents(text.lower()))
    counts = {language: sum(w in common for w in found) for language, common in _LANGUAGE_WORDS.items()}
    first, second = sorted(counts, key=counts.get, reverse=True)
    return first if counts[first] >= 3 and counts[first] >= 3 * counts[second] else None


def coverage(claim: str, source: str, *, stopwords: frozenset[str] = frozenset()) -> float:
    """How much of the claim's content appears in the source, in [0, 1].

    It is the measure used when an agent cites a source without quoting it: the
    share of the content words of the statement that the cited text also uses.
    A paraphrase keeps most of them; an invented requirement keeps almost none.
    An empty claim (only stopwords) counts as fully covered.
    """
    wanted = token_set(claim, stopwords=stopwords)
    if not wanted:
        return 1.0
    return len(wanted & token_set(source, stopwords=stopwords)) / len(wanted)


def sequence_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def containment(quote: str, source: str) -> float:
    """How much of the quote really appears in the source, in [0, 1].

    1.0 when the normalised quote is a substring of the normalised source. Below
    that, the longest common block measures partial support, which is enough to
    tell "a quote that was lightly reworded" from "a quote that was invented",
    the case worth catching.
    """
    q, s = normalize(quote), normalize(source)
    if not q:
        return 0.0
    if not s:
        return 0.0
    if q in s:
        return 1.0
    match = SequenceMatcher(None, q, s).find_longest_match(0, len(q), 0, len(s))
    return match.size / len(q)


# --------------------------------------------------------------------------
# Measurable quantities
# --------------------------------------------------------------------------

_NUMBER_WORDS = (
    r"one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|fifty|hundred|thousand|"
    r"um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|vinte|trinta|cem|mil"
)
#: Units of a measure: time, size, rate.
_MEASURE_UNITS = (
    r"ms|s|sec|secs|seconds?|seg|segundos?|min|mins|minutes?|minutos?|h|hrs?|hours?|horas?|"
    r"days?|dias?|weeks?|semanas?|months?|mes|meses|years?|anos?|"
    r"kb|mb|gb|tb|bytes?|px|fps|percent|por cento"
)
#: Units of a count. They only make a quantity with digits: "500 users" is a
#: quantity, "one item" is just a noun phrase.
_COUNT_UNITS = (
    r"req|requests?|requisicoes?|users?|usuarios?|characters?|chars?|caracteres?|digits?|digitos?|"
    r"items?|itens?|records?|registros?|rows?|linhas?|pages?|paginas?|results?|resultados?|"
    r"entries|entradas?|times|vezes|attempts?|tentativas?|retries|clicks?|cliques?|steps?|passos?"
)

# A number followed by a unit, a percentage or an explicit count. A spelled-out
# number ("two seconds") counts only with a unit of measure. "%" stays out of the
# final \b: there is no word boundary between "%" and the next space, and requiring
# one made "95% of the requests" stop counting as a quantity.
MEASURABLE_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:%|(?:" + _MEASURE_UNITS + "|" + _COUNT_UNITS + r")\b)"
    r"|\b(?:" + _NUMBER_WORDS + r")\s+(?:" + _MEASURE_UNITS + r")\b"
)
PLAIN_NUMBER_RE = re.compile(r"\b\d+([.,]\d+)?\b")


def has_measurable_quantity(text: str) -> bool:
    return bool(MEASURABLE_RE.search(normalize(text, keep_punct=True)))


def has_number(text: str) -> bool:
    return bool(PLAIN_NUMBER_RE.search(normalize(text, keep_punct=True)))
