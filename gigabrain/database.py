"""Local SQLite databases.

Three separate databases, one file each in the data folder:

1. Requirements Repository  (requirements.db)
   - requirement:          the living requirement (id R1, R2..., status, current version)
   - requirement_version:  every approved version; old versions are never deleted
   - requirement_link:     links between requirements ("R2 refines R1")
2. Expert Registry  (experts.db)
   - expert:            which experts exist and which topic each one covers
   - expert_knowledge:  every version of each expert's knowledge file
3. Log  (log.db)
   - conversation:  each session (with the PO or a processed meeting)
   - event:         each JSON message exchanged between the agents

Links read "source <kind> target", e.g. "R3 supersedes R1".

The three files are opened on a single connection (ATTACH), so queries stay simple.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata

from .messages import Message, now

LINK_KINDS = {
    "refines": "details the target, which stays valid",
    "splits": "is one of the parts the target was split into",
    "merges": "results from merging the target with other requirements",
    "supersedes": "takes the place of the target, which becomes obsolete",
    "depends_on": "needs the target to work",
    "conflicts_with": "contradicts the target; the PO must decide",
}

# Links that change the status of the target requirement.
STATUS_AFTER_LINK = {
    "supersedes": "superseded",
    "splits": "split",
    "merges": "merged",
}

# Links that tell where a requirement came from.
EVOLUTION_LINKS = ("refines", "splits", "merges", "supersedes")

SCHEMA = """
CREATE TABLE IF NOT EXISTS log.conversation (
    id          TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    mode        TEXT NOT NULL,           -- after_meeting | in_meeting | meeting_<mode>
    status      TEXT NOT NULL            -- open | approved | closed
);

CREATE TABLE IF NOT EXISTS main.requirement (
    id               TEXT PRIMARY KEY,   -- R1, R2, ...
    title            TEXT NOT NULL,
    kind             TEXT NOT NULL,      -- business | enabler
    topics           TEXT NOT NULL,      -- JSON: ["payments", ...]
    status           TEXT NOT NULL,      -- active | under_review | superseded | split | merged
    current_version  INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS main.requirement_version (
    requirement_id   TEXT NOT NULL REFERENCES requirement(id),
    version          INTEGER NOT NULL,
    content          TEXT NOT NULL,      -- JSON: title, statement, story, acceptance_criteria...
    sources          TEXT NOT NULL,      -- JSON: where each piece of information came from
    reason           TEXT,               -- why this version exists
    conversation_id  TEXT,               -- conversation in log.db
    approved_at      TEXT NOT NULL,
    PRIMARY KEY (requirement_id, version)
);

CREATE TABLE IF NOT EXISTS main.requirement_link (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id        TEXT NOT NULL REFERENCES requirement(id),
    kind             TEXT NOT NULL,
    target_id        TEXT NOT NULL REFERENCES requirement(id),
    reason           TEXT,
    conversation_id  TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS esp.expert (
    id                 TEXT PRIMARY KEY, -- topic slug: payments
    topic              TEXT NOT NULL,
    description        TEXT NOT NULL,
    file               TEXT NOT NULL,    -- path of the knowledge file
    knowledge_version  INTEGER NOT NULL DEFAULT 0,
    consultations      INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS esp.expert_knowledge (
    expert_id   TEXT NOT NULL REFERENCES expert(id),
    version     INTEGER NOT NULL,
    content     TEXT NOT NULL,           -- the whole Markdown of that version
    hash        TEXT NOT NULL,
    reason      TEXT NOT NULL,           -- creation | R3 saved | document ...
    origin      TEXT NOT NULL,           -- JSON with details of the trigger
    created_at  TEXT NOT NULL,
    PRIMARY KEY (expert_id, version)
);

CREATE TABLE IF NOT EXISTS log.event (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id       TEXT NOT NULL,
    conversation_id  TEXT,
    timestamp        TEXT NOT NULL,
    sender           TEXT NOT NULL,
    recipient        TEXT NOT NULL,
    kind             TEXT NOT NULL,
    content          TEXT NOT NULL       -- message JSON
);

CREATE INDEX IF NOT EXISTS log.idx_event_conversation ON event(conversation_id);
CREATE INDEX IF NOT EXISTS main.idx_link_source ON requirement_link(source_id);
CREATE INDEX IF NOT EXISTS main.idx_link_target ON requirement_link(target_id);
"""

# alias in the connection -> file
FILES = {"main": "requirements.db", "esp": "experts.db", "log": "log.db"}


def slug(text: str) -> str:
    """"Payment Notifications" -> "payment-notifications"."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-") or "general"


def _words(text: str) -> set[str]:
    return {w for w in slug(text).split("-") if len(w) > 3}


def has_database(folder: str) -> bool:
    return os.path.exists(os.path.join(folder, FILES["main"]))


class Database:
    def __init__(self, folder: str):
        os.makedirs(folder, exist_ok=True)
        self.folder = folder
        # timeout: with several conversations at once (API), wait for the other writer.
        self.con = sqlite3.connect(os.path.join(folder, FILES["main"]), check_same_thread=False, timeout=30)
        self.con.row_factory = sqlite3.Row
        for alias, file in FILES.items():
            if alias != "main":
                self.con.execute(f"ATTACH DATABASE ? AS {alias}", (os.path.join(folder, file),))
        self.con.execute("PRAGMA foreign_keys = ON")
        # WAL + synchronous NORMAL: every log event is a transaction; without this,
        # Windows waits for the disk on each message (slow with hundreds of them).
        for alias in FILES:
            self.con.execute(f"PRAGMA {alias}.journal_mode = WAL")
            self.con.execute(f"PRAGMA {alias}.synchronous = NORMAL")
        self.con.executescript(SCHEMA)

    def close(self) -> None:
        self.con.close()

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(row) for row in self.con.execute(sql, params)]

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        row = self.con.execute(sql, params).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ conversations

    def start_conversation(self, mode: str) -> str:
        id_ = "c-" + now().replace(":", "").replace("-", "").replace("T", "-")
        # Two conversations in the same second (e.g. tests) get a suffix.
        n, base = 1, id_
        while self._one("SELECT id FROM conversation WHERE id = ?", (id_,)):
            n += 1
            id_ = f"{base}-{n}"
        with self.con:
            self.con.execute(
                "INSERT INTO conversation (id, started_at, mode, status) VALUES (?, ?, ?, 'open')",
                (id_, now(), mode),
            )
        return id_

    def end_conversation(self, id_: str, status: str) -> None:
        with self.con:
            self.con.execute("UPDATE conversation SET ended_at = ?, status = ? WHERE id = ?", (now(), status, id_))

    def list_conversations(self) -> list[dict]:
        return self._all(
            """SELECT c.*, (SELECT COUNT(*) FROM event e WHERE e.conversation_id = c.id) AS events
               FROM conversation c ORDER BY c.started_at DESC"""
        )

    # ------------------------------------------------------------------ events (log)

    def record_event(self, msg: Message) -> None:
        with self.con:
            self.con.execute(
                """INSERT INTO event (message_id, conversation_id, timestamp, sender, recipient, kind, content)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (msg.id, msg.conversation_id, msg.timestamp, msg.sender, msg.recipient, msg.kind,
                 json.dumps(msg.content, ensure_ascii=False)),
            )

    def list_events(self, conversation_id: str | None = None, limit: int = 500) -> list[dict]:
        if conversation_id:
            rows = self._all("SELECT * FROM event WHERE conversation_id = ? ORDER BY id LIMIT ?", (conversation_id, limit))
        else:
            rows = self._all("SELECT * FROM event ORDER BY id DESC LIMIT ?", (limit,))
        for row in rows:
            row["content"] = json.loads(row["content"])
        return rows

    # ------------------------------------------------------------------ requirements

    def _next_requirement_id(self) -> str:
        row = self._one("SELECT MAX(CAST(SUBSTR(id, 2) AS INTEGER)) AS n FROM requirement")
        return f"R{(row['n'] or 0) + 1}"

    def save_requirement(
        self,
        content: dict,
        conversation_id: str | None,
        base_id: str | None = None,
        reason: str | None = None,
        links: list[dict] | None = None,
        sources: list[dict] | None = None,
    ) -> dict:
        """Save an approved requirement.

        - without `base_id`: creates a new requirement (R<n>, version 1)
        - with `base_id`: creates the next version of that requirement

        Returns {"requirement_id", "version", "links", "under_review"}, where
        `under_review` lists requirements that depend on this one and must be reviewed.
        """
        ts = now()
        topics = [slug(t) for t in content.get("topics") or ["general"]]
        title = content.get("title") or "(untitled)"
        kind = content.get("kind") or "business"
        under_review: list[str] = []

        with self.con:
            if base_id and self._one("SELECT id FROM requirement WHERE id = ?", (base_id,)):
                req_id = base_id
                version = self._one("SELECT current_version FROM requirement WHERE id = ?", (req_id,))["current_version"] + 1
                self.con.execute(
                    """UPDATE requirement SET title = ?, kind = ?, topics = ?, current_version = ?,
                       status = 'active', updated_at = ? WHERE id = ?""",
                    (title, kind, json.dumps(topics), version, ts, req_id),
                )
                # Whoever depended on the old version must be reviewed.
                under_review += self._mark_dependents(req_id, ts)
            else:
                req_id = self._next_requirement_id()
                version = 1
                self.con.execute(
                    """INSERT INTO requirement (id, title, kind, topics, status, current_version, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'active', 1, ?, ?)""",
                    (req_id, title, kind, json.dumps(topics), ts, ts),
                )

            content = {**content, "topics": topics}
            self.con.execute(
                """INSERT INTO requirement_version (requirement_id, version, content, sources, reason, conversation_id, approved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (req_id, version, json.dumps(content, ensure_ascii=False),
                 json.dumps(sources or [], ensure_ascii=False), reason, conversation_id, ts),
            )

            saved_links = []
            for link in links or []:
                made = self._insert_link(req_id, link.get("kind"), link.get("target"), link.get("reason"), conversation_id, ts)
                if made:
                    under_review += [r for r in made.pop("under_review") if r not in under_review]
                    saved_links.append(made)

        return {"requirement_id": req_id, "version": version, "links": saved_links, "under_review": under_review}

    def _insert_link(self, source: str, kind: str, target: str, reason: str | None,
                     conversation_id: str | None, ts: str) -> dict | None:
        if kind not in LINK_KINDS or not target or target == source:
            return None
        if not self._one("SELECT id FROM requirement WHERE id = ?", (target,)):
            return None
        self.con.execute(
            """INSERT INTO requirement_link (source_id, kind, target_id, reason, conversation_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source, kind, target, reason, conversation_id, ts),
        )
        under_review = []
        if kind in STATUS_AFTER_LINK:
            self.con.execute("UPDATE requirement SET status = ?, updated_at = ? WHERE id = ?",
                             (STATUS_AFTER_LINK[kind], ts, target))
            # The target is no longer valid: whoever depended on it must be reviewed.
            under_review = self._mark_dependents(target, ts, except_id=source)
        return {"source": source, "kind": kind, "target": target, "under_review": under_review}

    def link(self, source: str, kind: str, target: str, reason: str | None = None,
             conversation_id: str | None = None) -> dict | None:
        """Create a link between two existing requirements."""
        with self.con:
            return self._insert_link(source, kind, target, reason, conversation_id, now())

    def _mark_dependents(self, requirement_id: str, ts: str, except_id: str | None = None) -> list[str]:
        """Set "under_review" on requirements that `depends_on` this one."""
        marked = []
        for dep in self._all(
            "SELECT DISTINCT source_id FROM requirement_link WHERE target_id = ? AND kind = 'depends_on'", (requirement_id,)
        ):
            if dep["source_id"] == except_id:
                continue
            self.con.execute(
                "UPDATE requirement SET status = 'under_review', updated_at = ? WHERE id = ? AND status = 'active'",
                (ts, dep["source_id"]),
            )
            marked.append(dep["source_id"])
        return marked

    def _build_requirement(self, row: dict) -> dict:
        row["topics"] = json.loads(row["topics"])
        version = self._one("SELECT * FROM requirement_version WHERE requirement_id = ? AND version = ?",
                            (row["id"], row["current_version"]))
        row["content"] = json.loads(version["content"])
        row["sources"] = json.loads(version["sources"])
        return row

    def get_requirement(self, id_: str) -> dict | None:
        row = self._one("SELECT * FROM requirement WHERE id = ?", (id_,))
        return self._build_requirement(row) if row else None

    def list_requirements(self, only_active: bool = False) -> list[dict]:
        sql = "SELECT * FROM requirement"
        if only_active:
            sql += " WHERE status IN ('active', 'under_review')"
        sql += " ORDER BY CAST(SUBSTR(id, 2) AS INTEGER)"
        return [self._build_requirement(row) for row in self._all(sql)]

    def requirement_versions(self, id_: str) -> list[dict]:
        rows = self._all("SELECT * FROM requirement_version WHERE requirement_id = ? ORDER BY version", (id_,))
        for row in rows:
            row["content"] = json.loads(row["content"])
            row["sources"] = json.loads(row["sources"])
        return rows

    def list_links(self, requirement_id: str | None = None) -> list[dict]:
        if requirement_id:
            return self._all("SELECT * FROM requirement_link WHERE source_id = ? OR target_id = ? ORDER BY id",
                             (requirement_id, requirement_id))
        return self._all("SELECT * FROM requirement_link ORDER BY id")

    def trace(self, id_: str) -> list[dict]:
        """Answer "how did we get to this requirement?".

        Follows the evolution links backwards (R4 supersedes R3, R3 splits R1...)
        and returns the steps from oldest to newest, including the versions of
        every requirement on the way.
        """
        visited: set[str] = set()
        steps: list[dict] = []
        queue = [id_]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            for v in self.requirement_versions(current):
                steps.append({"timestamp": v["approved_at"], "requirement_id": current, "event": "version",
                              "version": v["version"], "title": v["content"].get("title"), "reason": v["reason"]})
            for link in self._all(
                f"""SELECT * FROM requirement_link WHERE source_id = ?
                    AND kind IN ({",".join("?" * len(EVOLUTION_LINKS))})""",
                (current, *EVOLUTION_LINKS),
            ):
                steps.append({"timestamp": link["created_at"], "requirement_id": current, "event": "link",
                              "kind": link["kind"], "target_id": link["target_id"], "reason": link["reason"]})
                queue.append(link["target_id"])
        # Ties in the same second: older requirements (smaller id) first, and a
        # requirement's version before the links it created.
        return sorted(steps, key=lambda s: (s["timestamp"], int(s["requirement_id"][1:]), s["event"] != "version", s.get("version", 0)))

    def search_requirements(self, text: str, topics: list[str] | None = None, limit: int = 8) -> list[dict]:
        """Simple search by words in common and by topic (no embeddings)."""
        wanted = _words(text)
        topic_slugs = {slug(t) for t in topics or []}
        scored = []
        for req in self.list_requirements():
            c = req["content"]
            req_text = " ".join([c.get("title", ""), c.get("statement", ""), json.dumps(c.get("story") or {})])
            score = len(wanted & _words(req_text)) + 3 * len(topic_slugs & set(req["topics"]))
            if score:
                scored.append((score, req))
        scored.sort(key=lambda s: -s[0])
        return [req for _, req in scored[:limit]]

    # ------------------------------------------------------------------ experts

    def create_expert(self, id_: str, topic: str, description: str, file: str) -> dict:
        ts = now()
        with self.con:
            self.con.execute(
                "INSERT INTO expert (id, topic, description, file, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (id_, topic, description, file, ts, ts),
            )
        return self.get_expert(id_)

    def get_expert(self, id_: str) -> dict | None:
        return self._one("SELECT * FROM expert WHERE id = ?", (id_,))

    def list_experts(self) -> list[dict]:
        return self._all("SELECT * FROM expert ORDER BY topic")

    def count_consultation(self, id_: str) -> None:
        with self.con:
            self.con.execute("UPDATE expert SET consultations = consultations + 1 WHERE id = ?", (id_,))

    def record_knowledge(self, id_: str, content: str, reason: str, origin: dict) -> int | None:
        """Store a new knowledge version. Returns the version, or None if nothing changed."""
        hash_ = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        last = self._one("SELECT hash FROM expert_knowledge WHERE expert_id = ? ORDER BY version DESC LIMIT 1", (id_,))
        if last and last["hash"] == hash_:
            return None
        ts = now()
        with self.con:
            version = self._one("SELECT knowledge_version FROM expert WHERE id = ?", (id_,))["knowledge_version"] + 1
            self.con.execute(
                """INSERT INTO expert_knowledge (expert_id, version, content, hash, reason, origin, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (id_, version, content, hash_, reason, json.dumps(origin, ensure_ascii=False), ts),
            )
            self.con.execute("UPDATE expert SET knowledge_version = ?, updated_at = ? WHERE id = ?", (version, ts, id_))
        return version

    def knowledge_dates(self, id_: str) -> list[str]:
        """When each knowledge version was created (so the dashboard can replay it in time)."""
        return [r["created_at"] for r in self._all(
            "SELECT created_at FROM expert_knowledge WHERE expert_id = ? ORDER BY version", (id_,))]

    def knowledge_versions(self, id_: str) -> list[dict]:
        rows = self._all("SELECT * FROM expert_knowledge WHERE expert_id = ? ORDER BY version", (id_,))
        for row in rows:
            row["origin"] = json.loads(row["origin"])
        return rows
