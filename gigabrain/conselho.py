"""Orquestra uma conversa inteira entre o PO e o GigaBrain.

Fluxo de uma fala do PO:

    PO ──fala──▶ Gêmeo ──(consultar?)──▶ Líder ──▶ Especialistas
                   ▲                       │
                   └──resposta consolidada─┘
                   │
                   ├─ perguntar ─▶ PO
                   └─ propor ────▶ PO ──"aprovado"──▶ salvar no Repositório
                                                      e atualizar os especialistas

Quando o PO aprova:
1. cada requisito proposto é salvo no banco (novo ou nova versão), com as
   fontes (conversa, especialistas consultados) e as ligações
2. requisitos que dependiam de um que mudou ficam "em_revisao"
3. os especialistas dos temas envolvidos atualizam o arquivo de conhecimento
"""

from __future__ import annotations

import os

from .agentes import Contexto, Especialista, GemeoDigital, Lider
from .aprovacao import eh_aprovacao
from .banco import Banco, slug
from .log import Registro
from .mensagens import GEMEO, LIDER, PO, REGISTRO, REPOSITORIO, SISTEMA, Mensagem

MAX_CONSULTAS_POR_FALA = 2
CAMPOS_DO_REQUISITO = ("titulo", "historia", "criterios_aceite", "tipo", "temas")


def abrir_contexto(pasta_dados: str, provedor, modo: str = "pos_reuniao", ver_agentes: bool = False) -> Contexto:
    os.makedirs(pasta_dados, exist_ok=True)
    banco = Banco(pasta_dados)
    registro = Registro(banco, os.path.join(pasta_dados, "logs"), ver_agentes=ver_agentes)
    return Contexto(banco, registro, provedor, os.path.join(pasta_dados, "conhecimento"), modo)


def _registrar(ctx: Contexto, de: str, para: str, tipo: str, conteudo: dict) -> None:
    ctx.registro.registrar(Mensagem(de=de, para=para, tipo=tipo, conteudo=conteudo, conversa_id=ctx.conversa_id))


class Conselho:
    def __init__(self, ctx: Contexto):
        self.ctx = ctx
        self.gemeo = GemeoDigital(ctx)
        self.lider = Lider(ctx)
        self.proposta_pendente: list[dict] | None = None
        self.fontes: list[dict] = []
        self.relacionados: list[str] = []

    def iniciar(self) -> str:
        self.ctx.conversa_id = self.ctx.banco.iniciar_conversa(self.ctx.modo)
        return self.ctx.conversa_id

    def encerrar(self, status: str = "encerrada") -> None:
        if self.ctx.conversa_id:
            self.ctx.banco.encerrar_conversa(self.ctx.conversa_id, status)

    # ------------------------------------------------------------------ conversa

    def falar(self, texto: str) -> dict:
        """Recebe uma fala do PO e devolve o que mostrar a ele.

        Retorno: {"acao": "perguntar"|"propor"|"aprovado", "mensagem_ao_po": ..., "requisitos": [...]}
        """
        if self.proposta_pendente and eh_aprovacao(texto):
            self.ctx.registro.registrar(Mensagem(PO, GEMEO, "aprovacao", {"texto": texto}, self.ctx.conversa_id))
            return self.aprovar()

        self.ctx.registro.registrar(Mensagem(PO, GEMEO, "pedido_po", {"texto": texto}, self.ctx.conversa_id))
        decisao = self.gemeo.ouvir_po(texto)

        consultas = 0
        while decisao["acao"] == "consultar":
            if consultas >= MAX_CONSULTAS_POR_FALA:
                decisao = self.gemeo.ouvir_sistema(
                    "Limite de consultas nesta rodada. Pergunte ao PO ou proponha com o que já sabe."
                )
                if decisao["acao"] == "consultar":
                    decisao = {"acao": "perguntar", "mensagem_ao_po": decisao.get("mensagem_ao_po", ""), "requisitos": []}
                break
            consulta = decisao.get("consulta") or {}
            resposta = self.lider.consultar(consulta.get("pergunta") or texto, consulta.get("temas") or [])
            self._guardar_fontes(resposta)
            consultas += 1
            decisao = self.gemeo.ouvir_lider(resposta)

        # Uma proposta só é trocada por outra proposta: se o Gêmeo responder
        # uma dúvida no meio, a última proposta continua valendo para aprovar.
        if decisao["acao"] == "propor":
            self.proposta_pendente = decisao["requisitos"]
        return decisao

    def _guardar_fontes(self, resposta: dict) -> None:
        for fonte in resposta.get("fontes", []):
            if fonte not in self.fontes:
                self.fontes.append(fonte)
        for rid in resposta.get("requisitos_relacionados", []):
            if rid not in self.relacionados:
                self.relacionados.append(rid)

    # ------------------------------------------------------------------ aprovação

    def aprovar(self) -> dict:
        if not self.proposta_pendente:
            return {"acao": "perguntar", "mensagem_ao_po": "Ainda não há proposta para aprovar.", "requisitos": []}

        banco = self.ctx.banco
        salvos = []
        for proposto in self.proposta_pendente:
            conteudo = {k: proposto[k] for k in CAMPOS_DO_REQUISITO if k in proposto}
            fontes = [{"tipo": "conversa", "ref": self.ctx.conversa_id}, *self.fontes]
            fontes += [{"tipo": "requisito", "ref": rid} for rid in self.relacionados]
            resultado = banco.salvar_requisito(
                conteudo,
                self.ctx.conversa_id,
                base_id=proposto.get("base"),
                motivo=proposto.get("motivo"),
                ligacoes=proposto.get("ligacoes"),
                fontes=fontes,
            )
            _registrar(self.ctx, GEMEO, REPOSITORIO, "requisito_salvo", {**resultado, "decidido_por": "po (aprovação explícita)"})
            for rid in resultado["em_revisao"]:
                _registrar(self.ctx, REPOSITORIO, GEMEO, "requisito_em_revisao", {"requisito_id": rid, "por_causa_de": resultado["requisito_id"]})
            self._atualizar_especialistas(resultado)
            salvos.append(resultado)

        self.proposta_pendente = None
        self.encerrar("aprovada")
        ids = ", ".join(f"{s['requisito_id']} v{s['versao']}" for s in salvos)
        return {"acao": "aprovado", "mensagem_ao_po": f"Requisito(s) salvo(s): {ids}.", "salvos": salvos, "requisitos": []}

    def _atualizar_especialistas(self, resultado: dict) -> None:
        """Avisa os especialistas de todos os temas tocados pelo requisito salvo."""
        banco = self.ctx.banco
        req = banco.obter_requisito(resultado["requisito_id"])
        temas = list(req["temas"])
        for lig in resultado["ligacoes"]:
            destino = banco.obter_requisito(lig["destino"])
            temas += [t for t in destino["temas"] if t not in temas]

        gatilho = {
            "tipo": "requisito_aprovado",
            "requisito": {
                "id": req["id"], "versao": req["versao_atual"], "titulo": req["titulo"],
                "motivo": banco.versoes_requisito(req["id"])[-1]["motivo"], **req["conteudo"],
            },
            "ligacoes": resultado["ligacoes"],
            "em_revisao": resultado["em_revisao"],
        }
        for tema in temas:
            esp = Especialista.carregar(self.ctx, slug(tema))
            if not esp:
                if self.ctx.modo == "reuniao":
                    _registrar(self.ctx, LIDER, REGISTRO, "pendencia", {"motivo": "requisito aprovado em tema sem especialista", "tema": tema})
                    continue
                # Ao nascer, o especialista já lê os requisitos do tema, inclusive este.
                Especialista.criar(self.ctx, tema, f"Regras de negócio e requisitos sobre {tema}.", criado_por=SISTEMA)
                continue
            esp.atualizar(gatilho, f"{req['id']} v{req['versao_atual']} aprovado")


def alimentar_especialista(ctx: Contexto, especialista_id: str, nome_documento: str, texto: str) -> int | None:
    """Entrega um documento do projeto (ata, regra de negócio, glossário) a um especialista."""
    esp = Especialista.carregar(ctx, especialista_id)
    if not esp:
        raise ValueError(f"especialista não encontrado: {especialista_id}")
    return esp.atualizar({"tipo": "documento", "nome": nome_documento, "texto": texto}, f"documento {nome_documento}")
