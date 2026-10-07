"""Cenário com várias reuniões, para medir o que o dataset original não mede.

O dataset traz cada projeto como UMA reunião. Aqui ela vira uma sequência de
sessões (2 ou 3 reuniões seguidas sobre o mesmo repositório) e, nas sessões
seguintes, são injetadas falas com gabarito conhecido:

- repetição: um stakeholder repete um requisito já dito numa sessão anterior
             ("As I said last time, ..."). O certo é NÃO salvar de novo.
- mudança:   um stakeholder muda um requisito de uma sessão anterior (outro
             número, ou uma restrição nova). O certo é salvar a versão nova e
             LIGÁ-LA ao requisito original (substitui, conflita_com ou refina).

A geração é determinística (semente fixa), sem LLM, para ser reprodutível.
As falas injetadas usam ids T9001, T9002... e ficam fora do cálculo de
precisão/revocação contra o gabarito original.
"""

from __future__ import annotations

import random
import re

from .avaliacao import avaliar as avaliar_extracao, similaridade
from .banco import Banco

FRASES_REPETICAO = [
    "As I said in the last meeting,",
    "Just to repeat what we agreed before,",
    "Like I mentioned last time,",
    "Again, as we discussed,",
]
FRASES_MUDANCA = [
    "Actually, we need to change something we agreed before:",
    "We talked to the team and want to change this:",
    "Change of plan on an earlier point:",
]
LIGACOES_DE_MUDANCA = ("substitui", "conflita_com", "refina")
ID_INJETADO = 9001


def _mudar(texto: str, rng: random.Random) -> str:
    """Muda o requisito de um jeito verificável: outro número, ou uma restrição nova."""
    numero = re.search(r"\b\d+\b", texto)
    if numero:
        novo = int(numero.group()) * 2 or 1
        return texto[:numero.start()] + str(novo) + texto[numero.end():]
    return texto.rstrip(". ") + rng.choice([
        ", but only for administrators.",
        ", but only after the user confirms it.",
        ", and this must also be logged.",
    ])


def gerar_sessoes(projeto: dict, n_sessoes: int = 3, fracao: float = 0.15, semente: int = 42) -> dict:
    turnos = projeto["turnos"]
    rng = random.Random(f"{projeto['id']}-{semente}")

    # Corta nas falas do Analyst (início de tópico) mais próximas de partes iguais.
    analyst = [i for i, t in enumerate(turnos) if t["falante"] == "Analyst" and i > 0]
    cortes = []
    for k in range(1, n_sessoes):
        alvo = round(len(turnos) * k / n_sessoes)
        cortes.append(min(analyst, key=lambda i: abs(i - alvo)) if analyst else alvo)
    cortes = sorted(set(c for c in cortes if 0 < c < len(turnos)))
    limites = [0, *cortes, len(turnos)]
    sessoes = [{"turnos": list(turnos[a:b])} for a, b in zip(limites, limites[1:])]
    sessao_do_turno = {t["id"]: k for k, s in enumerate(sessoes) for t in s["turnos"]}

    # Requisitos do gabarito que podem ser repetidos/mudados numa sessão posterior.
    candidatos = [g for g in projeto["gabarito"] if g["turnos"] and sessao_do_turno.get(g["turnos"][0], len(sessoes)) < len(sessoes) - 1]
    rng.shuffle(candidatos)
    k = max(1, round(len(projeto["gabarito"]) * fracao)) if candidatos else 0
    repetir, mudar = candidatos[:k], candidatos[k:2 * k]

    injecoes, proximo = [], ID_INJETADO
    falante = {t["id"]: t["falante"] for t in turnos}
    for tipo, lista in (("repeticao", repetir), ("mudanca", mudar)):
        for g in lista:
            origem = sessao_do_turno[g["turnos"][0]]
            destino = rng.randint(origem + 1, len(sessoes) - 1)
            texto = g["texto"] if tipo == "repeticao" else _mudar(g["texto"], rng)
            frase = rng.choice(FRASES_REPETICAO if tipo == "repeticao" else FRASES_MUDANCA)
            turno = {"id": f"T{proximo}", "falante": falante.get(g["turnos"][0], "Client"),
                     "texto": f"{frase} {texto}", "contexto": False}
            alvo = sessoes[destino]["turnos"]
            alvo.insert(rng.randint(1, len(alvo)), turno)
            injecoes.append({"turno": turno["id"], "tipo": tipo, "original": g["id"], "sessao": destino + 1,
                             "texto_original": g["texto"], "texto_injetado": texto})
            proximo += 1

    return {"projeto": projeto["id"], "semente": semente, "sessoes": sessoes, "injecoes": injecoes}


def avaliar(banco: Banco, projeto: dict, cenario: dict, previstos: list[dict]) -> dict:
    """Extração contra o gabarito (sem as falas injetadas) + repetições evitadas + mudanças ligadas."""
    injetados = {i["turno"]: i for i in cenario["injecoes"]}
    turnos = [t for s in cenario["sessoes"] for t in s["turnos"]]
    base = [p for p in previstos if not set(p["turnos"]) & injetados.keys()]
    relatorio = avaliar_extracao(base, projeto["gabarito"], turnos)
    requisito_do_gabarito = {x["gabarito"]: x["previsto"] for x in relatorio["pares"]}
    ligacoes = banco.listar_ligacoes()

    detalhes = []
    for inj in cenario["injecoes"]:
        salvos = [p for p in previstos if inj["turno"] in p["turnos"]]
        original = requisito_do_gabarito.get(inj["original"])
        item = {**inj, "salvos": [p["id"] for p in salvos], "requisito_original": original}
        if inj["tipo"] == "repeticao":
            # Evitada se nada do que foi salvo a partir da fala repete o requisito original.
            item["ok"] = not any(similaridade(p["texto"], inj["texto_original"]) >= 0.5 for p in salvos)
        else:
            item["capturada"] = bool(salvos)
            item["original_extraido"] = original is not None
            item["ok"] = bool(original) and any(
                l["origem_id"] in item["salvos"] and l["destino_id"] == original and l["tipo"] in LIGACOES_DE_MUDANCA
                for l in ligacoes
            )
        detalhes.append(item)

    rep = [d for d in detalhes if d["tipo"] == "repeticao"]
    mud = [d for d in detalhes if d["tipo"] == "mudanca"]
    mud_validas = [d for d in mud if d["original_extraido"]]
    taxa = lambda a, b: round(a / b, 4) if b else None
    relatorio["metricas"].update({
        "repeticoes_evitadas": taxa(sum(d["ok"] for d in rep), len(rep)),
        "mudancas_capturadas": taxa(sum(d["capturada"] for d in mud), len(mud)),
        "mudancas_ligadas": taxa(sum(d["ok"] for d in mud_validas), len(mud_validas)),
    })
    relatorio["injecoes"] = detalhes
    return relatorio
