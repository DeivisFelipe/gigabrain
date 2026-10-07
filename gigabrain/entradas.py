"""Leitura do dataset de entradas (repositório EntradasGigabrain).

Cada projeto do dataset é uma pasta com:

- transcricao_reuniao.txt   reunião simulada (Analyst, Client, TechLead, EndUser)
- gabarito_requisitos.json  requisitos corretos (padrão-ouro), com classe FR/NFR,
                            subtipo e os turnos da conversa de onde vieram
- requisitos_originais.pdf  documento original do PURE (não usado aqui)

O gabarito divide a transcrição em turnos T1, T2... por posição de caractere.
Usamos essa mesma divisão para que o GigaBrain cite turnos comparáveis.
"""

from __future__ import annotations

import csv
import json
import os

PASTA_PADRAO = os.environ.get(
    "GIGABRAIN_ENTRADAS",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "EntradasGigabrain"),
)


def listar_projetos(pasta: str = PASTA_PADRAO) -> list[dict]:
    indice = os.path.join(pasta, "indice_documentos.csv")
    if not os.path.exists(indice):
        raise FileNotFoundError(
            f"Dataset não encontrado em {pasta}. Clone https://github.com/schaumann-byte/EntradasGigabrain "
            "ao lado do gigabrain ou defina GIGABRAIN_ENTRADAS."
        )
    with open(indice, encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))
    # O índice abrevia alguns ids (2006-eirene-sys); usamos o nome real da pasta.
    pastas = {p for p in os.listdir(pasta) if os.path.isdir(os.path.join(pasta, p)) and not p.startswith(".")}
    for linha in linhas:
        linha["pasta"] = os.path.dirname(linha["arquivo_transcricao"]).replace("\\", "/").split("/")[-1]
        linha["disponivel"] = linha["pasta"] in pastas
    return linhas


def carregar_projeto(projeto: str, pasta: str = PASTA_PADRAO) -> dict:
    base = os.path.join(pasta, projeto)
    with open(os.path.join(base, "transcricao_reuniao.txt"), encoding="utf-8") as f:
        transcricao = f.read()
    with open(os.path.join(base, "gabarito_requisitos.json"), encoding="utf-8") as f:
        gabarito = json.load(f)

    turnos = []
    for seg in gabarito["input"]["segments"]:
        turnos.append({
            "id": seg["id"],
            "falante": seg["speaker"],
            "texto": transcricao[seg["start"]:seg["end"]].strip(),
            "contexto": seg.get("kind") == "context",
        })

    # Turno "principal" de cada requisito: onde ele é dito (gt_ids), não o contexto.
    turno_principal: dict[str, list[str]] = {}
    for seg in gabarito["input"]["segments"]:
        for gt in seg.get("gt_ids", []):
            turno_principal.setdefault(gt, []).append(seg["id"])

    requisitos = [
        {
            "id": r["id"],
            "texto": r["text"],
            "classe": r["class"],
            "subtipo": r.get("subtype"),
            "turnos": turno_principal.get(r["id"], []),
            "turnos_contexto": r.get("trace", {}).get("input_segments", []),
        }
        for r in gabarito["requirements"]
        if r.get("trace", {}).get("reachable", True)
    ]
    return {
        "id": gabarito["doc_id"],
        "pasta": projeto,
        "titulo": gabarito.get("doc_title", projeto),
        "turnos": turnos,
        "gabarito": requisitos,
    }


def transcricao_com_turnos(projeto: dict) -> str:
    """Texto que vai para o LLM: "[T4] EndUser: ..." uma fala por linha."""
    return "\n".join(f"[{t['id']}] {t['falante']}: {t['texto']}" for t in projeto["turnos"])
