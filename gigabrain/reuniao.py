"""Processa a transcrição de uma reunião inteira (modo pós-reunião em lote).

    transcrição ──▶ Gêmeo lista os rascunhos de requisito (citando turnos)
                       │
                       ├─ agente único (baseline): cada rascunho vai direto para o Repositório
                       │
                       └─ conselho, UM rascunho por vez:
                            Gêmeo ──consulta──▶ Líder ──▶ especialista do tema
                              ▲                               │ recomenda: manter, corrigir,
                              └──── recomendação ◀────────────┘ descartar, ligações
                            Gêmeo decide (voto final) ──▶ Repositório de Requisitos
                          o próximo rascunho já é revisado sabendo dos que foram salvos
                       │
                       ▼
          especialistas atualizam o conhecimento ──▶ avaliação contra o gabarito

Não há PO na reunião processada em lote: o Gêmeo Digital, que representa o PO no
Conselho Deliberativo, dá o voto final. O Conselho Consultivo só recomenda.
"""

from __future__ import annotations

import json
import os

from . import avaliacao
from .agentes import Contexto, Especialista, GemeoDigital, Lider
from .banco import slug
from .mensagens import GEMEO, LIDER, REPOSITORIO, SISTEMA, Mensagem


def _registrar(ctx: Contexto, de: str, para: str, tipo: str, conteudo: dict) -> None:
    ctx.registro.registrar(Mensagem(de=de, para=para, tipo=tipo, conteudo=conteudo, conversa_id=ctx.conversa_id))


def _salvar(ctx: Contexto, projeto: dict, rascunho: dict, final: dict, sugestao: dict | None) -> dict:
    conteudo = {
        "titulo": final["texto"][:80],
        "texto": final["texto"],
        "classe": final["classe"],
        "subtipo": final["subtipo"],
        "tipo": "negocio",
        "temas": [rascunho["tema"]],
        "turnos": rascunho["turnos"],
    }
    fontes = [{"tipo": "conversa", "ref": ctx.conversa_id}]
    fontes += [{"tipo": "turno", "ref": t, "projeto": projeto["id"]} for t in rascunho["turnos"]]
    if sugestao and sugestao.get("especialista_id"):
        fontes.append({"tipo": "especialista", "ref": sugestao["especialista_id"], "versao": sugestao.get("versao_conhecimento")})
    resultado = ctx.banco.salvar_requisito(conteudo, ctx.conversa_id, motivo=final.get("motivo") or "extraído da transcrição",
                                           ligacoes=final.get("ligacoes"), fontes=fontes)
    decidido = "gemeo (voto final após o conselho)" if sugestao else "gemeo (agente único, sem conselho)"
    _registrar(ctx, GEMEO, REPOSITORIO, "requisito_salvo", {**resultado, "decidido_por": decidido})
    for rid in resultado["em_revisao"]:
        _registrar(ctx, REPOSITORIO, GEMEO, "requisito_em_revisao", {"requisito_id": rid, "por_causa_de": resultado["requisito_id"]})
    return resultado


def processar(ctx: Contexto, projeto: dict, com_conselho: bool = True) -> dict:
    ctx.modo = "pos_reuniao"
    ctx.conversa_id = ctx.banco.iniciar_conversa("transcricao" if com_conselho else "transcricao_agente_unico")
    _registrar(ctx, SISTEMA, GEMEO, "transcricao", {"projeto": projeto["id"], "titulo": projeto["titulo"], "turnos": len(projeto["turnos"])})
    idioma = projeto.get("idioma", "en")
    falas = {t["id"]: t["texto"] for t in projeto["turnos"]}

    gemeo = GemeoDigital(ctx)
    lider = Lider(ctx) if com_conselho else None
    rascunhos = gemeo.extrair_da_transcricao(projeto)

    previstos, descartados, salvos_por_tema = [], [], {}
    for rascunho in rascunhos:
        sugestao = None
        if com_conselho:
            citadas = {t: falas[t] for t in rascunho["turnos"] if t in falas}
            gemeo.enviar(LIDER, "consulta", {
                "pergunta": f"rascunho {rascunho['indice'] + 1}/{len(rascunhos)}: {rascunho['texto'][:100]}",
                "rascunho": rascunho,
            })
            sugestao = lider.consultar_rascunho(rascunho, citadas, idioma)
            final = gemeo.decidir(rascunho, sugestao, citadas, idioma)
        else:
            final = {"salvar": True, "texto": rascunho["texto"], "classe": rascunho["classe"],
                     "subtipo": rascunho["subtipo"], "ligacoes": [], "motivo": "extraído da transcrição"}
        if not final["salvar"]:
            descartados.append({**rascunho, "recomendacao": sugestao, "motivo": final["motivo"]})
            continue
        resultado = _salvar(ctx, projeto, rascunho, final, sugestao)
        previstos.append({"id": resultado["requisito_id"], "texto": final["texto"], "classe": final["classe"],
                          "subtipo": final["subtipo"], "turnos": rascunho["turnos"], "tema": rascunho["tema"]})
        salvos_por_tema.setdefault(slug(rascunho["tema"]), []).append(previstos[-1])

    if com_conselho:
        _atualizar_especialistas(ctx, salvos_por_tema)
    ctx.banco.encerrar_conversa(ctx.conversa_id, "aprovada")
    return {"conversa_id": ctx.conversa_id, "previstos": previstos, "descartados": descartados}


def _atualizar_especialistas(ctx: Contexto, salvos_por_tema: dict[str, list[dict]]) -> None:
    """No fim da reunião, uma atualização por especialista com os requisitos do tema dele."""
    for tema_slug, grupo in salvos_por_tema.items():
        esp = Especialista.carregar(ctx, tema_slug)
        if not esp:
            continue
        esp.atualizar({
            "tipo": "lote_aprovado",
            "requisitos": [{"id": r["id"], "versao": 1, "titulo": r["texto"][:80], "classe": r["classe"]} for r in grupo],
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
        "descartados": resultado["descartados"],
        "gabarito": projeto["gabarito"],
    })
    with open(os.path.join(pasta_saida, "avaliacao.json"), "w", encoding="utf-8") as f:
        json.dump(relatorio, f, ensure_ascii=False, indent=1)
    return relatorio
