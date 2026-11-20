"""Turns a meeting transcript pasted by the PO into utterances.

The usual shape is one utterance per line, "Speaker: what was said", maybe with a time first
("[00:12:31] Ana: ..." or "00:12 Ana: ..."). A line without a speaker continues the utterance
before it. A transcript with no speaker at all becomes one utterance per paragraph.

The ids go on from the parts already sent in the conversation (T1, T2, ... T40, T41, ...),
so every requirement can point to the utterance it came from.
"""

from __future__ import annotations

import re

_TIME = r"(?:\[?\(?\d{1,2}:\d{2}(?::\d{2})?\)?\]?\s*[-–]?\s*)?"
# A speaker is a short name: up to 4 words, no sentence punctuation.
_SPEAKER = r"([A-Za-zÀ-ÿ][\wÀ-ÿ.'\-]*(?: [\wÀ-ÿ.'\-]+){0,3})"
_LINE = re.compile(rf"^\s*{_TIME}{_SPEAKER}\s*:\s*(.+)$")
UNKNOWN_SPEAKER = "Stakeholder"

# Who leads the meeting: their utterances are context for the others, not requirements.
LEADS = {"analyst", "analista", "interviewer", "entrevistador", "entrevistadora", "facilitator", "facilitador",
         "facilitadora", "moderator", "moderador", "moderadora", "requirements analyst", "analista de requisitos"}


def leads_the_meeting(speaker: str) -> bool:
    return speaker.strip().lower() in LEADS


def parse(text: str, start: int = 1) -> list[dict]:
    """Utterances of the transcript: [{"id": "T<n>", "speaker", "text"}], numbered from `start`."""
    turns: list[dict] = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        found = _LINE.match(line)
        if found:
            turns.append({"speaker": found.group(1).strip(), "text": found.group(2).strip()})
        elif turns:
            turns[-1]["text"] += " " + line.strip()
        else:
            turns.append({"speaker": None, "text": line.strip()})

    if all(t["speaker"] is None for t in turns):  # no speakers: one utterance per paragraph
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
        turns = [{"speaker": UNKNOWN_SPEAKER, "text": re.sub(r"\s+", " ", p)} for p in paragraphs]
    return [{"id": f"T{start + i}", "speaker": t["speaker"] or UNKNOWN_SPEAKER, "text": t["text"]}
            for i, t in enumerate(turns)]
