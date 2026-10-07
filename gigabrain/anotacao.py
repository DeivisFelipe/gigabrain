"""Anotação manual das ligações entre requisitos (para a QP3 do artigo).

O gabarito do dataset não diz nada sobre ligações (refina, depende_de...).
Para medir se elas estão corretas, duas pessoas do grupo avaliam uma amostra:

1. exportar:  sorteia N ligações das execuções do conselho e gera um CSV
              (abre no Excel/LibreOffice) com os dois requisitos lado a lado
2. anotar:    cada pessoa preenche a sua coluna com "c" (correta) ou "i" (incorreta),
              sem olhar a coluna da outra
3. kappa:     calcula a concordância entre as duas (kappa de Cohen) e a precisão
              das ligações segundo cada anotador
"""

from __future__ import annotations

import csv
import glob
import json
import os
import random
import sqlite3

COLUNAS = ["id", "execucao", "origem", "texto_origem", "fala_origem", "tipo", "destino", "texto_destino",
           "fala_destino", "motivo_do_sistema", "anotador_1", "anotador_2", "observacao"]
CORRETA = {"c", "correta", "certa", "1", "s", "sim", "y", "yes"}
INCORRETA = {"i", "incorreta", "errada", "0", "n", "nao", "não", "no"}


def _ligacoes_da_pasta(pasta: str) -> list[dict]:
    req = sqlite3.connect(os.path.join(pasta, "requisitos.db"))
    log = sqlite3.connect(os.path.join(pasta, "log.db"))
    try:
        falas = {}
        for (conteudo,) in log.execute("SELECT conteudo FROM evento WHERE tipo = 'fala'"):
            c = json.loads(conteudo)
            falas[c["turno"]] = f"{c['falante']}: {c['texto']}"
        textos, turnos = {}, {}
        for rid, conteudo in req.execute(
            "SELECT v.requisito_id, v.conteudo FROM requisito_versao v JOIN requisito r "
            "ON r.id = v.requisito_id AND r.versao_atual = v.versao"
        ):
            c = json.loads(conteudo)
            textos[rid] = c.get("texto") or c.get("historia") or c.get("titulo")
            turnos[rid] = c.get("turnos", [])
        fala = lambda rid: " | ".join(f"[{t}] {falas.get(t, '')}" for t in turnos.get(rid, []))
        return [
            {"origem": o, "tipo": t, "destino": d, "motivo_do_sistema": m or "",
             "texto_origem": textos.get(o, ""), "texto_destino": textos.get(d, ""),
             "fala_origem": fala(o), "fala_destino": fala(d)}
            for o, t, d, m in req.execute("SELECT origem_id, tipo, destino_id, motivo FROM requisito_ligacao ORDER BY id")
        ]
    finally:
        req.close()
        log.close()


def exportar(raiz: str, saida: str, amostra: int = 60, semente: int = 7, provedor: str = "*", cenario: str = "*") -> int:
    """Sorteia ligações das execuções do conselho e grava o CSV para anotação."""
    padrao = os.path.join(raiz, "dados-avaliacao", provedor, cenario, "*", "conselho", "rep-*")
    todas = []
    for pasta in sorted(glob.glob(padrao)):
        if not os.path.exists(os.path.join(pasta, "requisitos.db")):
            continue
        execucao = os.path.relpath(pasta, os.path.join(raiz, "dados-avaliacao")).replace("\\", "/")
        todas += [{"execucao": execucao, **l} for l in _ligacoes_da_pasta(pasta)]
    random.Random(semente).shuffle(todas)
    escolhidas = todas[:amostra]
    with open(saida, "w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig e ";" abrem direto no Excel em português
        escritor = csv.DictWriter(f, fieldnames=COLUNAS, delimiter=";")
        escritor.writeheader()
        for i, l in enumerate(escolhidas, start=1):
            escritor.writerow({"id": i, **l, "anotador_1": "", "anotador_2": "", "observacao": ""})
    return len(escolhidas)


def _rotulo(valor: str) -> str | None:
    v = (valor or "").strip().lower()
    return "c" if v in CORRETA else "i" if v in INCORRETA else None


def kappa(caminho: str) -> dict:
    """Kappa de Cohen entre anotador_1 e anotador_2, e a precisão das ligações por anotador."""
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        amostra = f.read(2048)
        f.seek(0)
        linhas = list(csv.DictReader(f, delimiter=";" if amostra.count(";") >= amostra.count(",") else ","))
    pares = [(_rotulo(l.get("anotador_1")), _rotulo(l.get("anotador_2"))) for l in linhas]
    pares = [(a, b) for a, b in pares if a and b]
    n = len(pares)
    if not n:
        return {"anotadas": 0}
    concordancia = sum(a == b for a, b in pares) / n
    p1 = sum(a == "c" for a, _ in pares) / n
    p2 = sum(b == "c" for _, b in pares) / n
    esperada = p1 * p2 + (1 - p1) * (1 - p2)
    k = 1.0 if esperada == 1 else (concordancia - esperada) / (1 - esperada)
    return {
        "anotadas": n,
        "concordancia": round(concordancia, 4),
        "kappa": round(k, 4),
        "precisao_anotador_1": round(p1, 4),
        "precisao_anotador_2": round(p2, 4),
        "precisao_consenso": round(sum(a == b == "c" for a, b in pares) / n, 4),
    }
