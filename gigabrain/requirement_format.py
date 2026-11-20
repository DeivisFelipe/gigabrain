"""Structure of a requirement and automatic quality checks.

Every requirement saved by GigaBrain combines widely used formats:

| Field                 | Format                                   | Purpose                                   |
|-----------------------|------------------------------------------|-------------------------------------------|
| statement             | "shall" sentence following an EARS pattern | unambiguous requirement (ISO/IEC/IEEE 29148) |
| story (FR only)       | As a <role>, I want <goal>, so that <benefit> | value for the user (agile)            |
| constraint (NFR only) | constraint card: quality, scope, measure | the limit the stories must respect        |
| acceptance_criteria   | Given / When / Then (Gherkin)            | how to test it (BDD)                      |
| class, subtype        | FR/NFR and ISO/IEC 25010 subtype         | classification                            |

A functional requirement (FR) says what the system does: a function, an input, an output, a
business rule. A non-functional requirement (NFR) says how well it must do it: performance,
security, usability, availability... An NFR is not a user story (no one wants a response time
for its own sake): it is a constraint card (Cohn, User Stories Applied, 2004) that limits the
stories it applies to and says how it is measured.

EARS (Easy Approach to Requirements Syntax, Mavin et al., RE 2009) patterns:

    ubiquitous  The <system> shall <response>.
    event       When <trigger>, the <system> shall <response>.
    state       While <state>, the <system> shall <response>.
    unwanted    If <problem>, then the <system> shall <response>.
    optional    Where <feature is present>, the <system> shall <response>.
    complex     a combination of the above (e.g. While ..., when ..., the ...)

The checks below are deterministic (no LLM). They work for English and
Portuguese, because the PO may talk in Portuguese.
"""

from __future__ import annotations

import re

EARS_PATTERNS = ("ubiquitous", "event", "state", "unwanted", "optional", "complex")
EMPTY_REQUIREMENT = {
    "title": "",
    "statement": "",
    "ears_pattern": "ubiquitous",
    "story": None,
    "constraint": None,
    "acceptance_criteria": [],
    "class": "FR",
    "subtype": None,
    "kind": "business",
    "topics": [],
}

# "shall/must" and the Portuguese "deve"/"deverá".
_MODAL = r"\b(shall|must|deve|deverá|devera)\b"
_TRIGGERS = {
    "event": r"^(when|quando)\b",
    "state": r"^(while|enquanto)\b",
    "unwanted": r"^(if|se)\b.*\b(then|então|entao)\b",
    "optional": r"^(where|onde)\b",
}

# Words that make a requirement impossible to verify unless a number goes with them.
VAGUE_TERMS = [
    "fast", "quick", "quickly", "easy", "easily", "user-friendly", "intuitive", "efficient", "adequate",
    "appropriate", "flexible", "robust", "simple", "as soon as possible", "etc",
    "rápido", "rapido", "fácil", "facil", "intuitivo", "amigável", "amigavel", "eficiente", "adequado",
    "apropriado", "flexível", "flexivel", "robusto", "simples", "o mais rápido possível",
]


def detect_ears(statement: str) -> str | None:
    """EARS pattern of the sentence, or None if it is not a "shall" sentence."""
    text = (statement or "").strip().lower()
    if not re.search(_MODAL, text):
        return None
    found = [name for name, pattern in _TRIGGERS.items() if re.search(pattern, text)]
    # "While ..., when ..." counts as complex.
    if re.match(r"^(while|enquanto)\b.*,\s*(when|quando)\b", text):
        return "complex"
    return found[0] if found else "ubiquitous"


def vague_terms(text: str) -> list[str]:
    t = (text or "").lower()
    found = []
    for term in VAGUE_TERMS:
        if re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", t):
            # "fast" followed by a number (e.g. "within 2 seconds") is fine.
            if not re.search(r"\d", t):
                found.append(term)
    return found


STORY_FIELDS = ("role", "goal", "benefit")
CONSTRAINT_FIELDS = ("quality", "scope", "measure")


def _complete(card, fields: tuple[str, ...]) -> bool:
    return isinstance(card, dict) and all((card.get(k) or "").strip() for k in fields)


def check(content: dict) -> dict:
    """Quality checks of a requirement content. All booleans are True when OK.

    "card" is the story for an FR and the constraint card for an NFR; the check of the
    other one is None (it does not apply).
    """
    nfr = str(content.get("class") or "").upper() == "NFR"
    criteria = content.get("acceptance_criteria") or []
    complete_criteria = [c for c in criteria if isinstance(c, dict) and all((c.get(k) or "").strip() for k in ("given", "when", "then"))]
    statement = content.get("statement") or ""
    story_complete = None if nfr else _complete(content.get("story"), STORY_FIELDS)
    constraint_complete = _complete(content.get("constraint"), CONSTRAINT_FIELDS) if nfr else None
    return {
        "ears": detect_ears(statement) is not None,
        "ears_pattern": detect_ears(statement),
        "card": "constraint" if nfr else "story",
        "card_complete": bool(constraint_complete if nfr else story_complete),
        "story_complete": story_complete,
        "constraint_complete": constraint_complete,
        "criteria_count": len(criteria),
        "criteria_gherkin": bool(criteria) and len(complete_criteria) == len(criteria),
        "vague_terms": vague_terms(" ".join([statement, *(c.get("then", "") for c in complete_criteria)])),
    }


def normalize(raw: dict, defaults: dict | None = None) -> dict:
    """Bring an LLM answer into the standard structure (missing fields get defaults)."""
    req = {**EMPTY_REQUIREMENT, **(defaults or {})}
    for key in req:
        if raw.get(key) not in (None, ""):
            req[key] = raw[key]
    req["class"] = "NFR" if str(req["class"]).upper() == "NFR" else "FR"
    if req["class"] == "FR":
        req["subtype"] = None
    story = req["story"] if isinstance(req["story"], dict) else None
    constraint = req["constraint"] if isinstance(req["constraint"], dict) else None
    # An FR is told as a story; an NFR is a constraint card, never a story.
    req["story"] = {k: str(story.get(k) or "") for k in STORY_FIELDS} if story and req["class"] == "FR" else None
    req["constraint"] = ({k: str(constraint.get(k) or "") for k in CONSTRAINT_FIELDS}
                         if constraint and req["class"] == "NFR" else None)
    req["acceptance_criteria"] = [
        {k: str(c.get(k, "")).strip() for k in ("scenario", "given", "when", "then")}
        for c in req["acceptance_criteria"] if isinstance(c, dict)
    ]
    if raw.get("ears_pattern") not in EARS_PATTERNS:
        req["ears_pattern"] = detect_ears(req["statement"]) or "ubiquitous"
    if not req["title"]:
        req["title"] = req["statement"][:80]
    return req


def summarize(requirements: list[dict]) -> dict:
    """How the saved requirements follow the structure: the share that passes each check and
    the ids that do not. `requirements` are rows of the Repository (id, content)."""
    checks = {
        "ears": lambda q: q["ears"],
        "card": lambda q: q["card_complete"],
        "gherkin": lambda q: q["criteria_gherkin"],
        "not_vague": lambda q: not q["vague_terms"],
    }
    results = [(r["id"], r["content"], check(r["content"])) for r in requirements]
    n = len(results)
    patterns: dict[str, int] = {}
    for _, _, q in results:
        key = q["ears_pattern"] or "not EARS"
        patterns[key] = patterns.get(key, 0) + 1
    return {
        "requirements": n,
        "fr": sum(1 for _, c, _ in results if str(c.get("class")).upper() != "NFR"),
        "nfr": sum(1 for _, c, _ in results if str(c.get("class")).upper() == "NFR"),
        "rates": {name: (round(sum(1 for *_, q in results if f(q)) / n, 4) if n else None) for name, f in checks.items()},
        "failing": {name: [rid for rid, _, q in results if not f(q)] for name, f in checks.items()},
        "ears_patterns": patterns,
        # An NFR told as a story is the mistake the format was changed to avoid.
        "nfr_with_story": [rid for rid, c, _ in results if str(c.get("class")).upper() == "NFR" and c.get("story")],
    }


def story_text(story: dict | None) -> str:
    if not story:
        return ""
    return f"As a {story.get('role')}, I want {story.get('goal')}, so that {story.get('benefit')}."


def constraint_text(constraint: dict | None) -> str:
    if not constraint:
        return ""
    return f"Constraint on {constraint.get('quality')} ({constraint.get('scope')}): {constraint.get('measure')}."
