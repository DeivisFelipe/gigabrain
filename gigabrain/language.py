"""Which language the PO is writing in, for the agents of a conversation.

A rule in the prompt was not enough: the first real runs answered a PO who wrote in English
in Portuguese, and the other agents followed. So the code works the language out, tells each
agent, and the Twin's answer is checked against it (agents/twin.py).

Only English and Portuguese are told apart, by common words of one language that the
other Romance languages do not use ("que" and "de" would make French look Portuguese). In any
other language nothing is said and nothing is checked. A meeting in batch does not use this:
its language comes with the project (dataset.py).

The Critic has its own `validation.text.language_of`, but it needs more evidence than a short
request has, and this must work when validation/ is off or broken.
"""

from __future__ import annotations

import re
import unicodedata

MARKERS = {
    "English": frozenset(
        """the and of to that is are for with when shall must this from by which it its an there be can should would
        need want we you they their our but only not about have has at""".split()
    ),
    "Portuguese": frozenset(
        """um uma uns umas os da das na nas em com ao aos pelo pela sao nao quando deve devem sobre qual quais voce seu
        sua preciso precisa quero mas existe existem alguma algum tem para esse essa isso ja somente tambem""".split()
    ),
}
MIN_MARKERS = 2  # "the date" says nothing; "it shows the date" does
LONG_ENOUGH = 8  # below this many words, a text does not show its language
# Measured on real answers: at least 8% in the language asked for, at most 4% in Italian, French or Spanish.
MIN_SHARE = 0.05


def _words(text: str) -> list[str]:
    plain = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))
    return re.findall(r"\w+", plain.lower())


def _count(found: list[str], language: str) -> int:
    return sum(w in MARKERS[language] for w in found)


def spoken_language(text: str) -> str | None:
    """"English", "Portuguese" or None when the message does not say clearly ("yes", another language)."""
    found = _words(text)
    first, second = sorted(MARKERS, key=lambda language: _count(found, language), reverse=True)
    most, other = _count(found, first), _count(found, second)
    return first if most >= MIN_MARKERS and most >= 2 * other else None


def written_in(text: str, language: str) -> bool:
    """False only when the text is long enough to tell and is not in `language`, whichever it is in."""
    found = _words(text)
    if len(found) < LONG_ENOUGH:
        return True
    ours = _count(found, language)
    others = max(_count(found, other) for other in MARKERS if other != language)
    return ours >= others and ours >= MIN_SHARE * len(found)
