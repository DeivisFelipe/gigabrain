"""Structure of a requirement and automatic quality checks.

Every requirement saved by GigaBrain combines three widely used formats:

| Field                 | Format                                   | Purpose                                   |
|-----------------------|------------------------------------------|-------------------------------------------|
| statement             | "shall" sentence following an EARS pattern | unambiguous requirement (ISO/IEC/IEEE 29148) |
| story                 | As a <role>, I want <goal>, so that <benefit> | value for the user (agile)            |
| acceptance_criteria   | Given / When / Then (Gherkin)            | how to test it (BDD)                      |
| class, subtype        | FR/NFR and ISO/IEC 25010 subtype         | classification                            |

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


def check(content: dict) -> dict:
    """Quality checks of a requirement content. All booleans are True when OK."""
    story = content.get("story") or {}
    criteria = content.get("acceptance_criteria") or []
    complete_criteria = [c for c in criteria if isinstance(c, dict) and all((c.get(k) or "").strip() for k in ("given", "when", "then"))]
    statement = content.get("statement") or ""
    return {
        "ears": detect_ears(statement) is not None,
        "ears_pattern": detect_ears(statement),
        "story_complete": all((story.get(k) or "").strip() for k in ("role", "goal", "benefit")),
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
    req["story"] = {k: (story or {}).get(k, "") for k in ("role", "goal", "benefit")} if story else None
    req["acceptance_criteria"] = [
        {k: str(c.get(k, "")).strip() for k in ("scenario", "given", "when", "then")}
        for c in req["acceptance_criteria"] if isinstance(c, dict)
    ]
    if raw.get("ears_pattern") not in EARS_PATTERNS:
        req["ears_pattern"] = detect_ears(req["statement"]) or "ubiquitous"
    if not req["title"]:
        req["title"] = req["statement"][:80]
    return req


def story_text(story: dict | None) -> str:
    if not story:
        return ""
    return f"As a {story.get('role')}, I want {story.get('goal')}, so that {story.get('benefit')}."
