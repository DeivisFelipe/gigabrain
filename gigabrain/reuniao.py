"""Processa a transcrição de uma reunião inteira (modo pós-reunião em lote).

    transcrição ──▶ Gêmeo extrai candidatos (citando turnos)
                       │
                       ├─ sem conselho (baseline de agente único): salva direto
                       │
                       └─ com conselho: Líder separa por tema ──▶ especialistas revisam
                                        (removem duplicados e sem respaldo, corrigem
                                        classe/subtipo, ligam requisitos do tema)
                       │
                       ▼
          Repositório de Requisitos ──▶ especialistas atualizam o conhecimento
                       │
                       ▼
          avaliação contra o gabarito do dataset (avaliacao.json)

Não há PO na reunião processada em lote: a aprovação é registrada como
automática, e cada requisito guarda os turnos de onde veio como fonte.
"""

from __future__ import annotations

import json
import os

from . import avaliacao
from .agentes import Contexto, Especialista, GemeoDigital, Lider
from .banco import slug
from .mensagens import GEMEO, SISTEMA, Mensagem


def _sistema(ctx: Contexto, tipo: str, conteudo: dict, para: str = "todos") -> None:
    ctx.registro.registrar(Mensagem(de=SISTEMA, para=para, tipo=tipo, conteudo=conteudo, conversa_id=ctx.conversa_id))


def processar(ctx: Contexto, projeto: dict, com_conselho: bool = True) -> dict:
    ctx.modo = "pos_reuniao"
    ctx.conversa_id = ctx.banco.iniciar_conversa("transcricao" if com_conselho else "transcricao_agente_unico")
    _sistema(ctx, "transcricao", {"projeto": projeto["id"], "titulo": projeto["titulo"], "turnos": len(projeto["turnos"])}, para=GEMEO)

    candidatos = GemeoDigital(ctx).extrair_da_transcricao(projeto)
    ligacoes = []
    if com_conselho:
        falas = {t["id"]: t["texto"] for t in projeto["turnos"]}
        revisao = Lider(ctx).revisar_extracao(candidatos, falas, projeto.get("idioma", "en"))
        candidatos, ligacoes = revisao["candidatos"], revisao["ligacoes"]

    # Salva cada requisito e guarda o mapa índice do candidato -> R<n>.
    ids: dict[int, str] = {}
    for c in candidatos:
        conteudo = {
            "titulo": c["texto"][:80],
            "texto": c["texto"],
            "classe": c["classe"],
            "subtipo": c["subtipo"],
            "tipo": "negocio",
            "temas": [c["tema"]],
            "turnos": c["turnos"],
        }
        fontes = [{"tipo": "conversa", "ref": ctx.conversa_id}]
        fontes += [{"tipo": "turno", "ref": t, "projeto": projeto["id"]} for t in c["turnos"]]
        if c.get("revisado_por"):
            fontes.append({"tipo": "especialista", "ref": c["revisado_por"]})
        resultado = ctx.banco.salvar_requisito(conteudo, ctx.conversa_id, motivo="extraído da transcrição", fontes=fontes)
        ids[c["indice"]] = resultado["requisito_id"]
        _sistema(ctx, "requisito_salvo", resultado)

    for lig in ligacoes:
        feita = ctx.banco.ligar(ids[lig["origem"]], lig.get("tipo"), ids[lig["destino"]], lig.get("motivo"), ctx.conversa_id)
        if feita:
            _sistema(ctx, "requisito_salvo", {"requisito_id": feita["origem"], "versao": None, "ligacoes": [feita]})

    if com_conselho:
        _atualizar_especialistas(ctx, candidatos, ids)
    ctx.banco.encerrar_conversa(ctx.conversa_id, "aprovada")

    return {
        "conversa_id": ctx.conversa_id,
        "previstos": [
            {"id": ids[c["indice"]], "texto": c["texto"], "classe": c["classe"], "subtipo": c["subtipo"],
             "turnos": c["turnos"], "tema": c["tema"]}
            for c in candidatos
        ],
    }


def _atualizar_especialistas(ctx: Contexto, candidatos: list[dict], ids: dict[int, str]) -> None:
    """Uma atualização por especialista, com todos os requisitos do tema dele."""
    por_tema: dict[str, list[dict]] = {}
    for c in candidatos:
        por_tema.setdefault(slug(c["tema"]), []).append(c)
    for tema_slug, grupo in por_tema.items():
        esp = Especialista.carregar(ctx, tema_slug)
        if not esp:
            continue
        esp.atualizar({
            "tipo": "lote_aprovado",
            "requisitos": [{"id": ids[c["indice"]], "versao": 1, "titulo": c["texto"][:80], "classe": c["classe"]} for c in grupo],
        }, f"{len(grupo)} requisito(s) da transcrição")


def processar_e_avaliar(ctx: Contexto, projeto: dict, pasta_saida: str, com_conselho: bool = True) -> dict:
    resultado = processar(ctx, projeto, com_conselho)
    relatorio = avaliacao.avaliar(resultado["previstos"], projeto["gabarito"], projeto["turnos"])
    relatorio.update({
        "projeto": projeto["id"],
        "titulo": projeto["titulo"],
        "modo": "conselho" if com_conselho else "agente_unico",
        "provedor": getattr(ctx.provedor, "nome", "?"),
        "conversa_id": resultado["conversa_id"],
        "previstos": resultado["previstos"],
        "gabarito": projeto["gabarito"],
    })
    with open(os.path.join(pasta_saida, "avaliacao.json"), "w", encoding="utf-8") as f:
        json.dump(relatorio, f, ensure_ascii=False, indent=1)
    return relatorio
