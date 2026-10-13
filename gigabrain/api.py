"""API REST do GigaBrain para a aplicação web de conversa com o PO.

Rodar:  python main.py api                (DeepSeek, porta 8000)
        python main.py api --simulado     (sem chave, respostas por regras)
Documentação interativa: http://127.0.0.1:8000/docs   (OpenAPI em /openapi.json)

Fluxo da conversa:

    POST /api/v1/conversas                       abre uma conversa -> {id}
    POST /api/v1/conversas/{id}/mensagens        o PO fala -> resposta do Gêmeo
         (pergunta, proposta de requisitos ou confirmação do que foi salvo)
    POST /api/v1/conversas/{id}/aprovar          aprova a proposta pendente (botão)
    POST /api/v1/conversas/{id}/encerrar         encerra sem aprovar

Histórico (somente leitura):

    GET  /api/v1/conversas[/{id}[/eventos]]      conversas, mensagens e bastidores
    GET  /api/v1/requisitos[/{id}]               requisitos, versões, ligações e trilha
    GET  /api/v1/ligacoes, /api/v1/especialistas[/{id}]

Cada conversa ativa fica na memória do servidor (o Gêmeo guarda o histórico
dela). Se a API for reiniciada, as conversas abertas param de aceitar
mensagens (409) e o PO abre outra; o histórico continua consultável.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import conhecimento
from .banco import Banco
from .conselho import Conselho, abrir_contexto
from .log import resumo as resumo_da_mensagem
from .mensagens import GEMEO, PO, Mensagem

# Mensagens que o PO vê na tela; o resto são os bastidores (agentes conversando).
TIPOS_DA_CONVERSA = ("pedido_po", "aprovacao", "pergunta_ao_po", "proposta", "confirmacao")
MODOS_DE_CONVERSA = ("pos_reuniao", "reuniao")


# ---------------------------------------------------------------------------- modelos

class NovaConversa(BaseModel):
    modo: Literal["pos_reuniao", "reuniao"] = Field(
        "pos_reuniao", description="pos_reuniao: pode criar especialistas novos. reuniao: rápido, só usa os existentes.")


class NovaMensagem(BaseModel):
    texto: str = Field(..., min_length=1, max_length=8000, description="O que o PO escreveu.",
                       examples=["Quero notificar o cliente quando o pagamento for confirmado"])


class Bastidor(BaseModel):
    id: int
    momento: str
    de: str
    para: str
    tipo: str
    resumo: str


class RespostaDoGemeo(BaseModel):
    acao: Literal["perguntar", "propor", "aprovado"] = Field(
        ..., description="perguntar: o Gêmeo precisa de mais informação. propor: há requisitos para o PO aprovar. "
                         "aprovado: os requisitos foram salvos e a conversa terminou.")
    mensagem: str = Field(..., description="Texto para mostrar ao PO.")
    requisitos_propostos: list[dict] = Field(default_factory=list,
                                             description="Com acao=propor: titulo, historia, criterios_aceite, tipo, temas, base, ligacoes.")
    salvos: list[str] = Field(default_factory=list, description="Com acao=aprovado: ids gravados (ex.: R3).")
    proposta_pendente: bool = Field(..., description="Se há proposta esperando aprovação (habilita o botão Aprovar).")
    conversa_ativa: bool
    bastidores: list[Bastidor] = Field(default_factory=list,
                                       description="O que os agentes fizeram para chegar a esta resposta.")


# ---------------------------------------------------------------------------- estado

@dataclass
class ConversaAtiva:
    conselho: Conselho
    trava: threading.Lock = field(default_factory=threading.Lock)


def _bastidor(ev: dict) -> dict:
    msg = SimpleNamespace(tipo=ev["tipo"], conteudo=ev["conteudo"])
    return {k: ev[k] for k in ("id", "momento", "de", "para", "tipo")} | {"resumo": resumo_da_mensagem(msg)}


def _mensagem_da_conversa(ev: dict) -> dict:
    c = ev["conteudo"]
    return {
        "id": ev["id"],
        "momento": ev["momento"],
        "autor": "po" if ev["de"] == PO else "gemeo",
        "tipo": ev["tipo"],
        "texto": c.get("texto", ""),
        "requisitos_propostos": c.get("requisitos", []) if ev["tipo"] == "proposta" else [],
        "salvos": c.get("salvos", []),
    }


def _resumo_requisito(r: dict) -> dict:
    c = r["conteudo"]
    return {
        "id": r["id"], "titulo": r["titulo"], "status": r["status"], "versao_atual": r["versao_atual"],
        "tipo": r["tipo"], "temas": r["temas"], "texto": c.get("texto") or c.get("historia"),
        "classe": c.get("classe"), "criado_em": r["criado_em"], "atualizado_em": r["atualizado_em"],
    }


# ---------------------------------------------------------------------------- aplicação

def criar_app(pasta_dados: str, criar_provedor: Callable[[], object], origens: list[str] | None = None,
              token: str | None = None) -> FastAPI:
    ativas: dict[str, ConversaAtiva] = {}
    trava_ativas = threading.Lock()

    @asynccontextmanager
    async def ciclo_de_vida(_app):
        yield
        for conversa_id in list(ativas):  # ao desligar, fecha as conexões das conversas abertas
            finalizar(conversa_id)

    app = FastAPI(
        lifespan=ciclo_de_vida,
        title="GigaBrain API",
        version="1.0.0",
        description="Conversa do PO real com o Gêmeo Digital e histórico do Repositório de Requisitos.",
    )
    app.add_middleware(CORSMiddleware, allow_origins=origens or ["*"], allow_methods=["*"], allow_headers=["*"])

    def autorizar(authorization: str | None = Header(None)) -> None:
        if token and authorization != f"Bearer {token}":
            raise HTTPException(401, "Token inválido ou ausente (cabeçalho Authorization: Bearer <token>).")

    def obter_banco():
        banco = Banco(pasta_dados)
        try:
            yield banco
        finally:
            banco.fechar()

    protegido = [Depends(autorizar)]

    def ativa(conversa_id: str) -> ConversaAtiva:
        with trava_ativas:
            sessao = ativas.get(conversa_id)
        if not sessao:
            raise HTTPException(409, "Esta conversa não está ativa (foi encerrada, aprovada ou a API reiniciou). Abra uma nova.")
        return sessao

    def finalizar(conversa_id: str) -> None:
        with trava_ativas:
            sessao = ativas.pop(conversa_id, None)
        if sessao:
            sessao.conselho.ctx.banco.fechar()

    def responder(sessao: ConversaAtiva, resposta: dict, desde: int) -> dict:
        ctx = sessao.conselho.ctx
        eventos = [e for e in ctx.banco.listar_eventos(ctx.conversa_id, limite=5000) if e["id"] > desde]
        corpo = {
            "acao": resposta["acao"],
            "mensagem": resposta.get("mensagem_ao_po", ""),
            "requisitos_propostos": resposta.get("requisitos", []) if resposta["acao"] == "propor" else [],
            "salvos": [s["requisito_id"] for s in resposta.get("salvos", [])],
            "proposta_pendente": bool(sessao.conselho.proposta_pendente),
            "conversa_ativa": resposta["acao"] != "aprovado",
            "bastidores": [_bastidor(e) for e in eventos if e["tipo"] not in TIPOS_DA_CONVERSA],
        }
        if resposta["acao"] == "aprovado":
            finalizar(ctx.conversa_id)
        return corpo

    def ultimo_evento(sessao: ConversaAtiva) -> int:
        linha = sessao.conselho.ctx.banco.con.execute(
            "SELECT COALESCE(MAX(id), 0) FROM evento WHERE conversa_id = ?", (sessao.conselho.ctx.conversa_id,)).fetchone()
        return linha[0]

    # ------------------------------------------------------------------ saúde

    @app.get("/api/v1/saude", tags=["sistema"])
    def saude() -> dict:
        provedor = criar_provedor()
        return {"status": "ok", "provedor": getattr(provedor, "nome", "?"), "conversas_ativas": len(ativas)}

    # ------------------------------------------------------------------ conversa

    @app.post("/api/v1/conversas", tags=["conversa"], status_code=201, dependencies=protegido)
    def abrir_conversa(dados: NovaConversa | None = None) -> dict:
        """Abre uma conversa do PO com o Gêmeo Digital."""
        modo = (dados or NovaConversa()).modo
        conselho = Conselho(abrir_contexto(pasta_dados, criar_provedor(), modo=modo))
        conversa_id = conselho.iniciar()
        with trava_ativas:
            ativas[conversa_id] = ConversaAtiva(conselho)
        return {"id": conversa_id, "modo": modo, "status": "aberta", "ativa": True}

    @app.post("/api/v1/conversas/{conversa_id}/mensagens", tags=["conversa"], response_model=RespostaDoGemeo,
              dependencies=protegido)
    def enviar_mensagem(conversa_id: str, dados: NovaMensagem) -> dict:
        """O PO envia uma fala. Pode levar de alguns segundos a ~1 minuto: o Gêmeo pode consultar
        o Líder e os especialistas antes de responder. "aprovado" (texto) também aprova a proposta pendente."""
        sessao = ativa(conversa_id)
        with sessao.trava:  # uma fala por vez em cada conversa
            desde = ultimo_evento(sessao)
            try:
                resposta = sessao.conselho.falar(dados.texto.strip())
            except Exception as exc:  # falha do LLM: a conversa continua utilizável
                raise HTTPException(502, f"O modelo de linguagem falhou: {exc}") from exc
            return responder(sessao, resposta, desde)

    @app.post("/api/v1/conversas/{conversa_id}/aprovar", tags=["conversa"], response_model=RespostaDoGemeo,
              dependencies=protegido)
    def aprovar(conversa_id: str) -> dict:
        """Aprova a proposta pendente (equivale ao PO escrever "aprovado")."""
        sessao = ativa(conversa_id)
        with sessao.trava:
            if not sessao.conselho.proposta_pendente:
                raise HTTPException(409, "Não há proposta pendente para aprovar.")
            desde = ultimo_evento(sessao)
            sessao.conselho.ctx.registro.registrar(
                Mensagem(PO, GEMEO, "aprovacao", {"texto": "aprovado (botão)"}, sessao.conselho.ctx.conversa_id))
            return responder(sessao, sessao.conselho.aprovar(), desde)

    @app.post("/api/v1/conversas/{conversa_id}/encerrar", tags=["conversa"], dependencies=protegido)
    def encerrar(conversa_id: str) -> dict:
        """Encerra a conversa sem aprovar nada."""
        sessao = ativa(conversa_id)
        with sessao.trava:
            sessao.conselho.encerrar("encerrada")
        finalizar(conversa_id)
        return {"id": conversa_id, "status": "encerrada", "ativa": False}

    # ------------------------------------------------------------------ histórico de conversas

    @app.get("/api/v1/conversas", tags=["histórico"], dependencies=protegido)
    def listar_conversas(somente_do_po: bool = Query(True, description="false inclui as reuniões processadas em lote"),
                         banco: Banco = Depends(obter_banco)) -> list[dict]:
        conversas = banco.listar_conversas()
        if somente_do_po:
            conversas = [c for c in conversas if c["modo"] in MODOS_DE_CONVERSA]
        return [{**c, "ativa": c["id"] in ativas} for c in conversas]

    @app.get("/api/v1/conversas/{conversa_id}", tags=["histórico"], dependencies=protegido)
    def obter_conversa(conversa_id: str, banco: Banco = Depends(obter_banco)) -> dict:
        """A conversa como o PO a vê: as mensagens dele e as respostas do Gêmeo."""
        conversa = next((c for c in banco.listar_conversas() if c["id"] == conversa_id), None)
        if not conversa:
            raise HTTPException(404, "Conversa não encontrada.")
        eventos = banco.listar_eventos(conversa_id, limite=5000)
        sessao = ativas.get(conversa_id)
        return {
            **conversa,
            "ativa": sessao is not None,
            "proposta_pendente": bool(sessao and sessao.conselho.proposta_pendente),
            "mensagens": [_mensagem_da_conversa(e) for e in eventos if e["tipo"] in TIPOS_DA_CONVERSA],
            "requisitos_salvos": [e["conteudo"]["requisito_id"] for e in eventos if e["tipo"] == "requisito_salvo"],
        }

    @app.get("/api/v1/conversas/{conversa_id}/eventos", tags=["histórico"], dependencies=protegido)
    def eventos_da_conversa(conversa_id: str, desde: int = Query(0, description="só eventos com id maior que este"),
                            completo: bool = Query(False, description="true inclui o JSON completo de cada mensagem"),
                            banco: Banco = Depends(obter_banco)) -> list[dict]:
        """Bastidores: todas as mensagens JSON entre os agentes, em ordem."""
        eventos = [e for e in banco.listar_eventos(conversa_id, limite=5000) if e["id"] > desde]
        return [_bastidor(e) | ({"conteudo": e["conteudo"]} if completo else {}) for e in eventos]

    # ------------------------------------------------------------------ requisitos

    @app.get("/api/v1/requisitos", tags=["histórico"], dependencies=protegido)
    def listar_requisitos(status: str | None = Query(None, description="ativo, em_revisao, substituido, dividido, unido"),
                          tema: str | None = None, busca: str | None = Query(None, description="texto livre"),
                          banco: Banco = Depends(obter_banco)) -> list[dict]:
        requisitos = [_resumo_requisito(r) for r in banco.listar_requisitos()]
        if status:
            requisitos = [r for r in requisitos if r["status"] == status]
        if tema:
            requisitos = [r for r in requisitos if tema in r["temas"]]
        if busca:
            b = busca.lower()
            requisitos = [r for r in requisitos if b in " ".join([r["id"], r["titulo"], r["texto"] or "", *r["temas"]]).lower()]
        return requisitos

    @app.get("/api/v1/requisitos/{requisito_id}", tags=["histórico"], dependencies=protegido)
    def obter_requisito(requisito_id: str, banco: Banco = Depends(obter_banco)) -> dict:
        """Conteúdo atual, todas as versões, ligações, fontes e a trilha ("como chegamos aqui")."""
        req = banco.obter_requisito(requisito_id)
        if not req:
            raise HTTPException(404, "Requisito não encontrado.")
        return {**req, "versoes": banco.versoes_requisito(requisito_id),
                "ligacoes": banco.listar_ligacoes(requisito_id), "trilha": banco.trilha(requisito_id)}

    @app.get("/api/v1/ligacoes", tags=["histórico"], dependencies=protegido)
    def listar_ligacoes(banco: Banco = Depends(obter_banco)) -> list[dict]:
        return banco.listar_ligacoes()

    # ------------------------------------------------------------------ especialistas

    @app.get("/api/v1/especialistas", tags=["histórico"], dependencies=protegido)
    def listar_especialistas(banco: Banco = Depends(obter_banco)) -> list[dict]:
        return banco.listar_especialistas()

    @app.get("/api/v1/especialistas/{especialista_id}", tags=["histórico"], dependencies=protegido)
    def obter_especialista(especialista_id: str, banco: Banco = Depends(obter_banco)) -> dict:
        """O arquivo de conhecimento atual e todas as versões dele."""
        esp = banco.obter_especialista(especialista_id)
        if not esp:
            raise HTTPException(404, "Especialista não encontrado.")
        return {**esp, "conhecimento": conhecimento.ler(esp["arquivo"]), "versoes": banco.versoes_conhecimento(especialista_id)}

    return app
