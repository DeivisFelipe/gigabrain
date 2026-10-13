"""Knowledge file of each expert.

Without access to code, an expert does not need RAG: the knowledge of a topic fits
in the prompt. Each expert has a Markdown file (data/knowledge/<id>.md) that anyone
can open, read and fix.

The file is the source of truth. The database keeps every version of it
(expert_knowledge), so it is possible to see how the knowledge evolved. If someone
edits the file by hand, the change is detected by its hash and becomes a new version
with the reason "manual edit".
"""

from __future__ import annotations

import os

# Above this size the expert is asked to summarize and reorganize the file.
MAX_CHARS = 12000

# Each saved requirement is written straight into the file (fast, no LLM). Every N
# notes, the expert uses the LLM to reorganize the whole file.
REORGANIZE_EVERY = 5

SECTIONS = [
    "Business rules",
    "Glossary",
    "Decisions (and why)",
    "Approved requirements",
    "Open points",
]

EMPTY = "- (empty)"

# Fixed knowledge of the quality expert (FR/NFR class and subtype).
NFR_TAXONOMY = """\
# Expert: quality (non-functional requirements)

> Checks whether each requirement is functional (FR) or non-functional (NFR) and, if NFR, its subtype.
> Based on ISO/IEC 25010 and the PURE/PROMISE taxonomy.

## Business rules
- FR: describes a behavior or function of the system (what it does, inputs, outputs, rules).
- NFR: describes a quality of how the system does something (how fast, secure, easy, available...).
- A requirement about what the system does with passwords or data (e.g. "the password has no length limit") is FR; it is NFR/SE only if it demands protection (encryption, authentication, access control).
- When in doubt between FR and NFR, choose FR.

## Glossary
- PE — performance/efficiency: response time, throughput, latency, resource usage.
- SE — security: authentication, authorization, encryption, privacy, auditing.
- US — usability: ease of learning and use, help, user messages.
- LF — look and feel: colors, fonts, layout, visual style.
- A — availability: uptime, 24/7, maintenance windows.
- SA — safety: avoid harm to people, the environment or accidental data loss.
- PO — portability: platforms, operating systems, browsers, devices.
- MN — maintainability: modularity, ease of change and testing.
- SC — scalability: growth in users, data or load.
- L — legal/compliance: laws, standards, licenses, regulations.
- FT — fault tolerance: recovery, backup, keep working under failures.
- OT — other quality not covered above.

## Decisions (and why)
- (empty)

## Approved requirements
- (empty)

## Open points
- (empty)
"""


def initial_template(topic: str, description: str) -> str:
    parts = [f"# Expert: {topic}", "", f"> {description}", ""]
    for section in SECTIONS:
        parts += [f"## {section}", EMPTY, ""]
    return "\n".join(parts)


def _section_bounds(lines: list[str], section: str) -> tuple[int, int] | None:
    start = next((i for i, l in enumerate(lines) if l.strip().lower() == f"## {section}".lower()), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def items(md: str, section: str) -> list[str]:
    lines = md.splitlines()
    bounds = _section_bounds(lines, section)
    if not bounds:
        return []
    start, end = bounds
    return [l[2:].strip() for l in lines[start + 1:end] if l.startswith("- ") and l.strip() != EMPTY]


def add_item(md: str, section: str, item: str) -> str:
    """Append "- item" at the end of the section (creates the section if missing)."""
    lines = md.rstrip("\n").splitlines()
    bounds = _section_bounds(lines, section)
    if not bounds:
        lines += ["", f"## {section}", f"- {item}"]
        return "\n".join(lines) + "\n"
    start, end = bounds
    body = [l for l in lines[start + 1:end] if l.strip() != EMPTY]
    if f"- {item}" in body:
        return md
    while body and not body[-1].strip():
        body.pop()
    body += [f"- {item}", ""]
    return "\n".join(lines[:start + 1] + body + lines[end:]).rstrip("\n") + "\n"


def path(folder: str, expert_id: str) -> str:
    return os.path.join(folder, f"{expert_id}.md")


def read(file: str) -> str:
    if not os.path.exists(file):
        return ""
    with open(file, encoding="utf-8") as f:
        return f.read()


def write(file: str, content: str) -> None:
    os.makedirs(os.path.dirname(file), exist_ok=True)
    with open(file, "w", encoding="utf-8") as f:
        f.write(content.rstrip("\n") + "\n")
