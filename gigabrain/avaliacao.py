"""Compara os requisitos que o GigaBrain extraiu com o gabarito do dataset.

Pareamento: cada requisito previsto é comparado com cada requisito do
gabarito pela sobreposição de palavras de conteúdo (coeficiente de Dice,
ignorando stopwords). Os pares são escolhidos do mais parecido para o menos,
um para um, enquanto a similaridade for >= LIMIAR.

Métricas:
- precisao     dos previstos, quantos existem no gabarito
- revocacao    do gabarito, quantos foram encontrados
- f1           média harmônica das duas
- classe       acerto FR/NFR entre os pares
- subtipo      acerto do subtipo entre os pares que são NFR no gabarito
- rastreio     pares em que algum turno citado é o turno onde o requisito foi dito
- sem_respaldo previstos que não citam turno, ou cujos turnos citados quase não
               têm palavras em comum com o requisito (sinal de alucinação)
"""

from __future__ import annotations

import re

LIMIAR = 0.5
LIMIAR_RESPALDO = 0.3

STOPWORDS = set("""
a an the and or but if then else of to in on at by for with from as is are was were be been being
it its this that these those there here which who whom what when where why how all any each both
shall should must will would could can may might need needs needed have has had do does did done
not no nor so such than too very just also only own same other some more most into over under
about above below between through during before after again further once i we you he she they them
our your their my me us his her uh um uhm erm like know mean sort well okay ok yeah yes right
""".split())


def palavras(texto: str) -> set[str]:
    return {p for p in re.findall(r"[a-z0-9]+", texto.lower()) if p not in STOPWORDS and len(p) > 1}


def similaridade(a: str, b: str) -> float:
    pa, pb = palavras(a), palavras(b)
    if not pa or not pb:
        return 0.0
    return 2 * len(pa & pb) / (len(pa) + len(pb))


def _cobertura(requisito: str, fala: str) -> float:
    """Quanto das palavras do requisito aparece na fala citada."""
    pr = palavras(requisito)
    return len(pr & palavras(fala)) / len(pr) if pr else 0.0


def _razao(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def avaliar(previstos: list[dict], gabarito: list[dict], turnos: list[dict]) -> dict:
    """previstos/gabarito: [{"id", "texto", "classe", "subtipo", "turnos"}]."""
    falas = {t["id"]: t["texto"] for t in turnos}

    candidatos = []
    for i, p in enumerate(previstos):
        for j, g in enumerate(gabarito):
            s = similaridade(p["texto"], g["texto"])
            if s >= LIMIAR:
                candidatos.append((s, i, j))
    candidatos.sort(reverse=True)

    usados_p, usados_g, pares = set(), set(), []
    for s, i, j in candidatos:
        if i in usados_p or j in usados_g:
            continue
        usados_p.add(i)
        usados_g.add(j)
        p, g = previstos[i], gabarito[j]
        pares.append({
            "previsto": p["id"],
            "gabarito": g["id"],
            "similaridade": round(s, 3),
            "classe_ok": p.get("classe") == g["classe"],
            "subtipo_ok": (p.get("subtipo") == g["subtipo"]) if g["classe"] == "NFR" else None,
            "rastreio_ok": bool(set(p.get("turnos") or []) & set(g["turnos"])),
        })

    sem_respaldo = []
    for p in previstos:
        citadas = " ".join(falas.get(t, "") for t in p.get("turnos") or [])
        if not citadas or _cobertura(p["texto"], citadas) < LIMIAR_RESPALDO:
            sem_respaldo.append(p["id"])

    nfr = [x for x in pares if x["subtipo_ok"] is not None]
    acertos = len(pares)
    precisao = _razao(acertos, len(previstos))
    revocacao = _razao(acertos, len(gabarito))
    f1 = round(2 * precisao * revocacao / (precisao + revocacao), 4) if precisao and revocacao else 0.0
    return {
        "metricas": {
            "previstos": len(previstos),
            "gabarito": len(gabarito),
            "acertos": acertos,
            "precisao": precisao,
            "revocacao": revocacao,
            "f1": f1,
            "classe": _razao(sum(x["classe_ok"] for x in pares), acertos),
            "subtipo": _razao(sum(x["subtipo_ok"] for x in nfr), len(nfr)),
            "rastreio": _razao(sum(x["rastreio_ok"] for x in pares), acertos),
            "sem_respaldo": _razao(len(sem_respaldo), len(previstos)),
        },
        "pares": pares,
        "nao_encontrados": [g["id"] for j, g in enumerate(gabarito) if j not in usados_g],
        "falsos_positivos": [p["id"] for i, p in enumerate(previstos) if i not in usados_p],
        "sem_respaldo": sem_respaldo,
        "parametros": {"limiar_similaridade": LIMIAR, "limiar_respaldo": LIMIAR_RESPALDO},
    }
