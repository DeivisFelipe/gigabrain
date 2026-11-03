"""Projects of the PO. Each project is a folder with its own databases, logs and expert files.

    <data>/projects.json            the registry: id, name, description, created_at
    <data>/projects/<id>/           requirements.db, experts.db, log.db, knowledge/, logs/

Requirements, conversations and experts of one project never mix with another's.
"""

from __future__ import annotations

import json
import os
import re
import threading
import unicodedata

from .messages import now

REGISTRY = "projects.json"
NAME_LIMIT = 80


class ProjectError(ValueError):
    pass


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "project"


class Projects:
    def __init__(self, data_folder: str):
        self.root = data_folder
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ storage

    def _path(self) -> str:
        return os.path.join(self.root, REGISTRY)

    def _read(self) -> list[dict]:
        try:
            with open(self._path(), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return []

    def _write(self, projects: list[dict]) -> None:
        os.makedirs(self.root, exist_ok=True)
        tmp = self._path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(projects, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path())

    # ------------------------------------------------------------------ operations

    def list(self) -> list[dict]:
        return self._read()

    def get(self, project_id: str) -> dict | None:
        return next((p for p in self._read() if p["id"] == project_id), None)

    def folder(self, project_id: str) -> str:
        """The data folder of the project; ProjectError if it does not exist."""
        if not self.get(project_id):
            raise ProjectError(f"Project not found: {project_id}")
        return os.path.join(self.root, "projects", project_id)

    def create(self, name: str, description: str = "") -> dict:
        name = " ".join((name or "").split())
        if not name:
            raise ProjectError("The project needs a name.")
        if len(name) > NAME_LIMIT:
            raise ProjectError(f"The name can have at most {NAME_LIMIT} characters.")
        with self._lock:
            projects = self._read()
            if any(p["name"].casefold() == name.casefold() for p in projects):
                raise ProjectError(f"There is already a project called '{name}'.")
            taken, base, n = {p["id"] for p in projects}, slugify(name), 1
            project_id = base
            while project_id in taken:
                n += 1
                project_id = f"{base}-{n}"
            project = {"id": project_id, "name": name, "description": (description or "").strip(), "created_at": now()}
            os.makedirs(os.path.join(self.root, "projects", project_id), exist_ok=True)
            self._write([*projects, project])
            return project

    def update(self, project_id: str, name: str | None = None, description: str | None = None) -> dict:
        with self._lock:
            projects = self._read()
            project = next((p for p in projects if p["id"] == project_id), None)
            if not project:
                raise ProjectError(f"Project not found: {project_id}")
            if name is not None:
                name = " ".join(name.split())
                if not name:
                    raise ProjectError("The project needs a name.")
                if len(name) > NAME_LIMIT:
                    raise ProjectError(f"The name can have at most {NAME_LIMIT} characters.")
                if any(p["name"].casefold() == name.casefold() and p["id"] != project_id for p in projects):
                    raise ProjectError(f"There is already a project called '{name}'.")
                project["name"] = name
            if description is not None:
                project["description"] = description.strip()
            self._write(projects)
            return project
