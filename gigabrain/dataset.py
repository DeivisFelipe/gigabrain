"""Reads the test dataset (repository EntradasGigabrain).

Each project of the dataset is a folder with:

- transcricao_reuniao.txt   simulated meeting (Analyst, Client, TechLead, EndUser)
- gabarito_requisitos.json  gold standard requirements, with FR/NFR class, subtype and
                            the turns of the conversation they came from
- requisitos_originais.pdf  original PURE document (not used here)

The file and column names are the dataset's own (in Portuguese) and are kept as they are.

The gold standard splits the transcript into turns T1, T2... by character position.
GigaBrain uses the same split so that it cites comparable turns.
"""

from __future__ import annotations

import csv
import json
import os
import re

DEFAULT_FOLDER = os.environ.get(
    "GIGABRAIN_DATASET",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "EntradasGigabrain"),
)
INDEX_FILE = "indice_documentos.csv"
TRANSCRIPT_FILE = "transcricao_reuniao.txt"
GOLD_FILE = "gabarito_requisitos.json"


def list_projects(folder: str = DEFAULT_FOLDER) -> list[dict]:
    index = os.path.join(folder, INDEX_FILE)
    if not os.path.exists(index):
        raise FileNotFoundError(
            f"Dataset not found in {folder}. Clone https://github.com/schaumann-byte/EntradasGigabrain "
            "next to gigabrain or set GIGABRAIN_DATASET."
        )
    with open(index, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # The index abbreviates some ids (2006-eirene-sys); use the real folder name.
    folders = {p for p in os.listdir(folder) if os.path.isdir(os.path.join(folder, p)) and not p.startswith(".")}
    projects = []
    for row in rows:
        name = os.path.dirname(row["arquivo_transcricao"]).replace("\\", "/").split("/")[-1]
        projects.append({
            "folder": name,
            "name": row.get("nome_projeto", name),
            "requirements": int(row.get("requisitos_dialogo") or 0),
            "words": int(row.get("palavras_transcricao") or 0),
            "tier": row.get("qualidade_tier", ""),
            "available": name in folders,
        })
    return projects


def load_project(project: str, folder: str = DEFAULT_FOLDER) -> dict:
    base = os.path.join(folder, project)
    with open(os.path.join(base, TRANSCRIPT_FILE), encoding="utf-8") as f:
        transcript = f.read()
    with open(os.path.join(base, GOLD_FILE), encoding="utf-8") as f:
        gold_file = json.load(f)

    turns = [{"id": seg["id"], "speaker": seg["speaker"], "text": transcript[seg["start"]:seg["end"]].strip(),
              "context": seg.get("kind") == "context"} for seg in gold_file["input"]["segments"]]

    # "Main" turn of each requirement: where it is said (gt_ids), not the context.
    main_turn: dict[str, list[str]] = {}
    for seg in gold_file["input"]["segments"]:
        for gt in seg.get("gt_ids", []):
            main_turn.setdefault(gt, []).append(seg["id"])

    gold = [
        {"id": r["id"], "text": r["text"], "class": r["class"], "subtype": r.get("subtype"),
         "turns": main_turn.get(r["id"], []), "context_turns": r.get("trace", {}).get("input_segments", [])}
        for r in gold_file["requirements"]
        if r.get("trace", {}).get("reachable", True)
    ]
    return {
        "language": detect_language(transcript),
        "id": gold_file["doc_id"],
        "folder": project,
        "title": gold_file.get("doc_title", project),
        "turns": turns,
        "gold": gold,
    }


WORDS_EN = {"the", "and", "we", "to", "of", "is", "that", "it", "should", "would", "need", "what", "you"}
WORDS_PT = {"o", "de", "que", "e", "do", "da", "em", "um", "para", "precisa", "deve", "você", "não"}


def detect_language(text: str) -> str:
    """"en" or "pt", counting very common words of each language."""
    found = re.findall(r"[a-zà-ú]+", text.lower())
    en = sum(w in WORDS_EN for w in found)
    pt = sum(w in WORDS_PT for w in found)
    return "pt" if pt > en else "en"
