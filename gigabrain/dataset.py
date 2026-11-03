"""Reads the test dataset (repository EntradasGigabrain).

The dataset comes in variants with growing noise (index column "variante", or
EntradasGigabrain/README.md): v1-original, the reference, and rules-* and llm-*. A copy of the
dataset without variants (tag v1.0) is read as v1-original.

Each project of v1-original is a folder with:

- transcricao_reuniao.txt   simulated meeting (Analyst, Client, TechLead, EndUser)
- gabarito_requisitos.json  gold standard requirements (schema 1.0), with FR/NFR class,
                            subtype and the turns of the conversation they came from

Each project of the other variants has one or more meetings and a richer gold standard:

- reuniao_01.txt, ...       the meetings
- gabarito_requisitos.json  schema 2.0: what should come out (expected_in_output) and what
                            should not (rejected and omitted requirements, distractors)

The file and column names are the dataset's own (in Portuguese) and are kept as they are.

The gold standard splits the transcript into turns T1, T2... (M1:T1, M2:T1... in schema 2.0) by
character position. GigaBrain uses the same split so that it cites comparable turns.
"""

from __future__ import annotations

import csv
import json
import os
import re

_PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The dataset is looked for next to the project and, if it is not there, inside it.
_BESIDE = os.path.join(os.path.dirname(_PROJECT), "EntradasGigabrain")
_INSIDE = os.path.join(_PROJECT, "EntradasGigabrain")

DEFAULT_FOLDER = os.environ.get(
    "GIGABRAIN_DATASET",
    _INSIDE if os.path.isdir(_INSIDE) and not os.path.isdir(_BESIDE) else _BESIDE,
)
INDEX_FILE = "indice_documentos.csv"
TRANSCRIPT_FILE = "transcricao_reuniao.txt"
GOLD_FILE = "gabarito_requisitos.json"
DEFAULT_VARIANT = "v1-original"
VARIANT_PARENTS = ("", "variante_A", "variante_B")  # where the folder of a variant may be


class DatasetError(ValueError):
    """The dataset does not have what was asked for (e.g. an unknown variant)."""


def _read_index(folder: str) -> list[dict]:
    index = os.path.join(folder, INDEX_FILE)
    if not os.path.exists(index):
        raise FileNotFoundError(
            f"Dataset not found in {folder}. Clone https://github.com/schaumann-byte/EntradasGigabrain "
            "next to gigabrain or set GIGABRAIN_DATASET."
        )
    with open(index, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def list_variants(folder: str = DEFAULT_FOLDER) -> list[str]:
    rows = _read_index(folder)
    if rows and "variante" in rows[0]:
        return list(dict.fromkeys(r["variante"] for r in rows))
    return [DEFAULT_VARIANT]


def _unknown_variant(folder: str, variant: str) -> DatasetError:
    return DatasetError(f"Variant '{variant}' not found in {folder}. Available: {', '.join(list_variants(folder))}.")


def list_projects(folder: str = DEFAULT_FOLDER, variant: str = DEFAULT_VARIANT) -> list[dict]:
    rows = _read_index(folder)
    if rows and "variante" in rows[0]:
        return _list_variant(folder, rows, variant)
    if variant != DEFAULT_VARIANT:
        raise _unknown_variant(folder, variant)
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


def _list_variant(folder: str, rows: list[dict], variant: str) -> list[dict]:
    """The projects of one variant, from the index with one row per (variant, project)."""
    rows = [r for r in rows if r["variante"] == variant]
    if not rows:
        raise _unknown_variant(folder, variant)
    return [{
        "folder": os.path.basename(r["pasta"].replace("\\", "/")),
        "name": r.get("nome_projeto", r["id_documento"]),
        "requirements": int(r.get("requisitos_esperados") or 0),
        "words": int(r.get("palavras_transcricao") or 0),
        "tier": r.get("qualidade_tier", ""),
        "meetings": int(r.get("n_reunioes") or 1),
        "available": os.path.isdir(os.path.join(folder, r["pasta"])),
    } for r in rows]


def _variant_folder(folder: str, variant: str) -> str:
    for parent in VARIANT_PARENTS:
        candidate = os.path.join(folder, parent, variant)
        if os.path.isdir(candidate):
            return candidate
    if variant == DEFAULT_VARIANT:  # a dataset without variant folders (tag v1.0)
        return folder
    raise _unknown_variant(folder, variant)


def load_project(project: str, folder: str = DEFAULT_FOLDER, variant: str = DEFAULT_VARIANT) -> dict:
    base = os.path.join(_variant_folder(folder, variant), project)
    with open(os.path.join(base, GOLD_FILE), encoding="utf-8") as f:
        gold_file = json.load(f)
    if str(gold_file.get("schema_version", "1")).startswith("2"):
        return _load_meetings(gold_file, base, project, variant)

    with open(os.path.join(base, TRANSCRIPT_FILE), encoding="utf-8") as f:
        transcript = f.read()

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


def _load_meetings(gold_file: dict, base: str, project: str, variant: str) -> dict:
    """A project in schema 2.0: its meetings, one after the other, and the richer gold standard.

    "turns" has every turn of every meeting (turn ids are unique across meetings), and
    "meetings" has them split, so that each meeting can be processed on its own.
    "gold" has only what should come out, the same denominator as the dataset's evaluator;
    "negatives" has what should not (see evaluation_meetings.py).
    """
    meetings, transcripts = [], []
    main_turn: dict[str, list[str]] = {}
    for meeting in gold_file["input"]["meetings"]:
        with open(os.path.join(base, meeting["file"]), encoding="utf-8") as f:
            transcript = f.read()
        transcripts.append(transcript)
        meetings.append({"id": meeting["id"], "kind": meeting.get("kind"), "turns": [
            {"id": seg["id"], "speaker": seg["speaker"], "text": transcript[seg["start"]:seg["end"]].strip(),
             "context": seg.get("act") == "context"} for seg in meeting["segments"]
        ]})
        for seg in meeting["segments"]:
            for gt in seg.get("gt_ids", []):
                main_turn.setdefault(gt, []).append(seg["id"])

    requirements = gold_file["requirements"]
    gold = [
        {"id": r["id"], "text": r["text"], "class": r["class"], "subtype": r.get("subtype"), "status": r.get("status"),
         "turns": main_turn.get(r["id"], []), "context_turns": r.get("trace", {}).get("input_segments", []),
         "phenomena": r.get("phenomena", [])}
        for r in requirements if r.get("expected_in_output", True)
    ]
    negatives = [{"id": r["id"], "text": r["text"], "kind": r.get("status")} for r in requirements if not r.get("expected_in_output", True)]
    negatives += [{"id": d["id"], "text": d["text"], "kind": "distractor"} for d in gold_file.get("distractors", [])]
    return {
        "language": detect_language("\n".join(transcripts)),
        "id": gold_file["doc_id"],
        "folder": project,
        "title": gold_file.get("doc_title", project),
        "turns": [t for m in meetings for t in m["turns"]],
        "gold": gold,
        "variant": variant,
        "meetings": meetings,
        "negatives": negatives,
        "issues": gold_file.get("issues", []),
    }


WORDS_EN = {"the", "and", "we", "to", "of", "is", "that", "it", "should", "would", "need", "what", "you"}
WORDS_PT = {"o", "de", "que", "e", "do", "da", "em", "um", "para", "precisa", "deve", "você", "não"}


def detect_language(text: str) -> str:
    """"en" or "pt", counting very common words of each language."""
    found = re.findall(r"[a-zà-ú]+", text.lower())
    en = sum(w in WORDS_EN for w in found)
    pt = sum(w in WORDS_PT for w in found)
    return "pt" if pt > en else "en"
