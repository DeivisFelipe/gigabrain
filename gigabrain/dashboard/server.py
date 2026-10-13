"""Dashboard server (standard library only, no dependencies).

One dashboard for every data folder: starting from a root folder, the server finds
every subfolder with a database (requirements.db): data/, data-demo/,
data-eval/<provider>/<scenario>/<project>/<mode>/rep-<n>/... The page chooses which one
to show with the parameter ?source=<relative path>.

    GET /api/sources                             data folders found
    GET /api/benchmark                           every evaluation (repetitions, scenarios) + statistics
    GET /api/summary?source=...                  counts and list of conversations
    GET /api/events?source=...&conversation=     messages of a conversation, in order
    GET /api/requirements?source=...             requirements + links
    GET /api/requirement/<id>?source=...         versions, links, trace and quality of a requirement
    GET /api/experts?source=...                  expert registry
    GET /api/expert/<id>?source=...              current file + all versions
    GET /api/evaluation?source=...               result against the gold standard, if any
"""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .. import knowledge, requirement_format, stats
from ..database import FILES, Database

FOLDER = os.path.dirname(os.path.abspath(__file__))
IGNORE = {".git", "venv", ".venv", "__pycache__", "node_modules", "knowledge", "logs"}
MAX_DEPTH = 7
EVALUATION_FILE = "evaluation.json"


def find_sources(root: str) -> list[dict]:
    """Every folder (up to 7 levels below the root) that holds a GigaBrain database."""
    sources = []
    for folder, subfolders, files in os.walk(root):
        rel = os.path.relpath(folder, root).replace("\\", "/")
        depth = 0 if rel == "." else rel.count("/") + 1
        subfolders[:] = sorted(s for s in subfolders if s not in IGNORE) if depth < MAX_DEPTH else []
        if FILES["main"] not in files:
            continue
        parts = [] if rel == "." else rel.split("/")
        sources.append({
            "id": rel,
            "group": parts[0] if parts else os.path.basename(root),
            "label": " / ".join(parts[1:]) or (parts[0] if parts else "."),
            "evaluation": EVALUATION_FILE in files,
        })
    return sources


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _identify(source_id: str) -> dict | None:
    """data-eval/<provider>/<scenario>/<project>/<mode>/rep-<n>"""
    parts = source_id.split("/")
    if len(parts) != 6 or parts[0] != "data-eval" or not parts[5].startswith("rep-"):
        return None
    return {"provider": parts[1], "scenario": parts[2], "project": parts[3], "mode": parts[4], "rep": int(parts[5][4:])}


def benchmark(root: str) -> dict:
    """Gather every evaluation (with repetitions and scenarios) and compute the statistics."""
    runs, in_progress = [], []
    for source in find_sources(root):
        info = _identify(source["id"])
        if not info:
            continue
        try:
            metrics = _read_json(os.path.join(root, source["id"], EVALUATION_FILE))["metrics"] if source["evaluation"] else None
        except (OSError, json.JSONDecodeError, KeyError):
            metrics = None
        if metrics is None:
            in_progress.append(source["id"])
            continue
        runs.append({"source": source["id"], **info, **metrics})

    groups: dict[str, list] = {}
    for r in runs:
        groups.setdefault(f"{r['provider']}/{r['scenario']}", []).append(r)
    return {"runs": runs, "in_progress": in_progress,
            "statistics": {key: stats.summarize(rows) for key, rows in groups.items()}}


def _routes(db: Database, folder: str, parts: list[str], query: dict) -> object:
    if parts == ["evaluation"]:
        path = os.path.join(folder, EVALUATION_FILE)
        return _read_json(path) if os.path.exists(path) else {}
    if parts == ["summary"]:
        requirements = db.list_requirements()
        return {
            "conversations": db.list_conversations(),
            "requirements": len(requirements),
            "by_status": {s: sum(r["status"] == s for r in requirements) for s in {r["status"] for r in requirements}},
            "experts": len(db.list_experts()),
            "links": len(db.list_links()),
        }
    if parts == ["events"]:
        conversation = (query.get("conversation") or [None])[0]
        events = db.list_events(conversation, limit=5000)
        return events if conversation else list(reversed(events))
    if parts == ["requirements"]:
        return {"requirements": db.list_requirements(), "links": db.list_links()}
    if len(parts) == 2 and parts[0] == "requirement":
        req = db.get_requirement(parts[1])
        if not req:
            return None
        return {**req, "quality": requirement_format.check(req["content"]), "versions": db.requirement_versions(req["id"]),
                "links": db.list_links(req["id"]), "trace": db.trace(req["id"])}
    if parts == ["experts"]:
        return [{**e, "versions_at": db.knowledge_dates(e["id"])} for e in db.list_experts()]
    if len(parts) == 2 and parts[0] == "expert":
        exp = db.get_expert(parts[1])
        if not exp:
            return None
        return {**exp, "content": knowledge.read(exp["file"]), "versions": db.knowledge_versions(exp["id"])}
    return None


def make_handler(root: str):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # silence the default log of every request
            pass

        def _respond(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: object) -> None:
            if data is None:
                return self._respond(404, b'{"error": "not found"}', "application/json")
            self._respond(200, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                with open(os.path.join(FOLDER, "index.html"), "rb") as f:
                    return self._respond(200, f.read(), "text/html; charset=utf-8")
            if not url.path.startswith("/api/"):
                return self._respond(404, b"not found", "text/plain")

            parts = [p for p in url.path.split("/") if p][1:]  # drop "api"
            query = parse_qs(url.query)
            if parts == ["sources"]:
                return self._json(find_sources(root))
            if parts == ["benchmark"]:
                return self._json(benchmark(root))

            # Only accept sources that really exist (never leave the root folder).
            source = (query.get("source") or [""])[0]
            if source not in {s["id"] for s in find_sources(root)}:
                return self._json(None)
            folder = os.path.join(root, source)
            with lock:
                db = Database(folder)
                try:
                    data = _routes(db, folder, parts, query)
                finally:
                    db.close()
            self._json(data)

    return Handler


def serve(root: str, port: int = 8765, open_browser: bool = True) -> None:
    root = os.path.abspath(root)
    sources = find_sources(root)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(root))
    url = f"http://127.0.0.1:{port}/"
    print(f"GigaBrain dashboard at {url}  ({len(sources)} data folder(s) in {root})  Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
