"""Processa a transcrição de uma reunião, fala por fala (modo pós-reunião em lote).

    para cada fala de stakeholder:
      Reunião ──fala──▶ Gêmeo rascunha os requisitos daquela fala
                          │
                          ├─ agente único (baseline): salva o rascunho direto
                          │
                          └─ conselho, um rascunho por vez:
                               Gêmeo ──consulta──▶ Líder ──▶ especialista do tema
                                 ▲                               │ recomenda: manter, corrigir,
                                 └──── recomendação ◀────────────┘ descartar, ligações
                               Gêmeo decide (voto final) ──▶ Repositório de Requisitos
                               especialista aprende na hora (atualiza o conhecimento)
      ──▶ próxima fala

Não há PO na reunião processada em lote: o Gêmeo Digital, que representa o PO no
Conselho Deliberativo, dá o voto final. O Conselho Consultivo só recomenda.
"""

from __future__ import annotations

import json
import os
import shutil

from . import avaliacao, cenarios
from .agentes import Contexto, Especialista, GemeoDigital, Lider
from .banco import slug
from .mensagens import GEMEO, LIDER, REPOSITORIO, SISTEMA, Mensagem


def _registrar(ctx: Contexto, de: str, para: str, tipo: str, conteudo: dict) -> None:
    ctx.registro.registrar(Mensagem(de=de, para=para, tipo=tipo, conteudo=conteudo, conversa_id=ctx.conversa_id))


MODOS = ("conselho", "agente_unico", "llm_puro")
NOME_CONVERSA = {"conselho": "transcricao", "agente_unico": "transcricao_agente_unico", "llm_puro": "transcricao_llm_puro"}
DECIDIDO_POR = {
    "conselho": "gemeo (voto final após o conselho)",
    "agente_unico": "gemeo (agente único, sem conselho)",
    "llm_puro": "llm puro (baseline, sem conselho)",
}


def _salvar(ctx: Contexto, projeto: dict, rascunho: dict, final: dict, sugestao: dict | None, modo: str) -> dict:
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
    _registrar(ctx, GEMEO, REPOSITORIO, "requisito_salvo", {**resultado, "decidido_por": DECIDIDO_POR[modo]})
    for rid in resultado["em_revisao"]:
        _registrar(ctx, REPOSITORIO, GEMEO, "requisito_em_revisao", {"requisito_id": rid, "por_causa_de": resultado["requisito_id"]})
    return resultado


def _ja_salvos(ctx: Contexto) -> list[dict]:
    """Tudo o que já está no Repositório (inclusive de reuniões anteriores)."""
    return [{"id": r["id"], "texto": r["conteudo"].get("texto") or r["titulo"]}
            for r in ctx.banco.listar_requisitos(apenas_ativos=True)]


def processar(ctx: Contexto, projeto: dict, modo: str = "conselho") -> dict:
    """Processa uma reunião. modo: "conselho", "agente_unico" ou "llm_puro"."""
    if isinstance(modo, bool):  # compatibilidade: processar(ctx, projeto, True/False)
        modo = "conselho" if modo else "agente_unico"
    ctx.modo = "pos_reuniao"
    ctx.conversa_id = ctx.banco.iniciar_conversa(NOME_CONVERSA[modo])
    _registrar(ctx, SISTEMA, GEMEO, "transcricao", {"projeto": projeto["id"], "titulo": projeto["titulo"], "turnos": len(projeto["turnos"])})
    idioma = projeto.get("idioma", "en")
    falas = {t["id"]: t["texto"] for t in projeto["turnos"]}
    gemeo = GemeoDigital(ctx)
    previstos, descartados = [], []

    def guardar(rascunho: dict, final: dict, sugestao: dict | None) -> None:
        if not final["salvar"]:
            descartados.append({**rascunho, "recomendacao": sugestao, "motivo": final["motivo"]})
            return
        resultado = _salvar(ctx, projeto, rascunho, final, sugestao, modo)
        previstos.append({"id": resultado["requisito_id"], "texto": final["texto"], "classe": final["classe"],
                          "subtipo": final["subtipo"], "turnos": rascunho["turnos"], "tema": rascunho["tema"]})
        if modo == "conselho":
            esp = Especialista.carregar(ctx, slug(rascunho["tema"]))
            if esp:  # o especialista do tema aprende na hora
                esp.registrar_requisito({"id": resultado["requisito_id"], "texto": final["texto"], "classe": final["classe"],
                                         "motivo": final.get("motivo")}, resultado["ligacoes"], resultado["em_revisao"])

    if modo == "llm_puro":
        for rascunho in gemeo.extrair_tudo_de_uma_vez(projeto):
            guardar(rascunho, {**rascunho, "salvar": True, "ligacoes": [], "motivo": "extraído pelo LLM puro"}, None)
    else:
        lider = Lider(ctx) if modo == "conselho" else None
        total = 0
        for i, turno in enumerate(projeto["turnos"]):
            if turno["falante"] == "Analyst":  # o Analyst só conduz; vira contexto das próximas falas
                continue
            _registrar(ctx, SISTEMA, GEMEO, "fala", {"turno": turno["id"], "falante": turno["falante"], "texto": turno["texto"]})
            rascunhos = gemeo.rascunhar(projeto, i, _ja_salvos(ctx), total)
            total += len(rascunhos)
            for rascunho in rascunhos:  # cada rascunho vai até o fim antes do próximo
                if lider:
                    citadas = {t: falas[t] for t in rascunho["turnos"] if t in falas}
                    gemeo.enviar(LIDER, "consulta", {"pergunta": f"rascunho da fala {turno['id']}: {rascunho['texto'][:100]}", "rascunho": rascunho})
                    sugestao = lider.consultar_rascunho(rascunho, citadas, idioma)
                    guardar(rascunho, gemeo.decidir(rascunho, sugestao, citadas, idioma), sugestao)
                else:
                    guardar(rascunho, {**rascunho, "salvar": True, "ligacoes": [], "motivo": "extraído da transcrição"}, None)

    ctx.banco.encerrar_conversa(ctx.conversa_id, "aprovada")
    return {"conversa_id": ctx.conversa_id, "previstos": previstos, "descartados": descartados}


def _relatorio(ctx: Contexto, projeto: dict, modo: str, base: dict) -> dict:
    return {
        **base,
        "projeto": projeto["id"],
        "titulo": projeto["titulo"],
        "modo": modo,
        "provedor": getattr(ctx.provedor, "nome", "?"),
        "gabarito": projeto["gabarito"],
    }


def _gravar(pasta: str, relatorio: dict) -> dict:
    with open(os.path.join(pasta, "avaliacao.json"), "w", encoding="utf-8") as f:
        json.dump(relatorio, f, ensure_ascii=False, indent=1)
    return relatorio


def processar_e_avaliar(ctx: Contexto, projeto: dict, pasta_saida: str, modo: str = "conselho") -> dict:
    if isinstance(modo, bool):
        modo = "conselho" if modo else "agente_unico"
    resultado = processar(ctx, projeto, modo)
    relatorio = avaliacao.avaliar(resultado["previstos"], projeto["gabarito"], projeto["turnos"])
    relatorio.update({"cenario": "reuniao", "conversa_id": resultado["conversa_id"],
                      "previstos": resultado["previstos"], "descartados": resultado["descartados"]})
    return _gravar(pasta_saida, _relatorio(ctx, projeto, modo, relatorio))


def processar_sessoes_e_avaliar(ctx: Contexto, projeto: dict, cenario: dict, pasta_saida: str, modo: str = "conselho") -> dict:
    """Cenário com várias reuniões: as sessões rodam em sequência sobre o MESMO repositório."""
    previstos, descartados, conversas = [], [], []
    n = len(cenario["sessoes"])
    for k, sessao in enumerate(cenario["sessoes"], start=1):
        parte = {**projeto, "titulo": f"{projeto['titulo']} (sessão {k}/{n})", "turnos": sessao["turnos"]}
        r = processar(ctx, parte, modo)
        previstos += r["previstos"]
        descartados += r["descartados"]
        conversas.append(r["conversa_id"])
    relatorio = cenarios.avaliar(ctx.banco, projeto, cenario, previstos)
    relatorio.update({"cenario": "sessoes", "conversas": conversas, "previstos": previstos, "descartados": descartados})
    return _gravar(pasta_saida, _relatorio(ctx, projeto, modo, relatorio))


def migrar_pastas_antigas(raiz_provedor: str) -> None:
    """dados-avaliacao/<prov>/<projeto>/<modo>/ -> dados-avaliacao/<prov>/reuniao/<projeto>/<modo>/rep-1/"""
    if not os.path.isdir(raiz_provedor):
        return
    for projeto in os.listdir(raiz_provedor):
        base = os.path.join(raiz_provedor, projeto)
        if projeto in ("reuniao", "sessoes") or not os.path.isdir(base):
            continue
        for modo in os.listdir(base):
            antiga = os.path.join(base, modo)
            if os.path.isdir(antiga) and not os.path.isdir(os.path.join(antiga, "rep-1")):
                os.makedirs(os.path.join(raiz_provedor, "reuniao", projeto, modo), exist_ok=True)
                shutil.move(antiga, os.path.join(raiz_provedor, "reuniao", projeto, modo, "rep-1"))
        if not os.listdir(base):
            os.rmdir(base)
