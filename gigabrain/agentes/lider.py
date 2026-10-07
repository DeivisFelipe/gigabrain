"""Líder: o único agente fixo do Conselho Consultivo.

Quando o Gêmeo faz uma consulta, o Líder:

1. decompõe a pergunta por tema de negócio, olhando o Registro de
   Especialistas (quem já existe)
2. para cada tema, usa o especialista existente ou cria um novo
   (no modo "reuniao" não cria: registra uma pendência para depois)
3. manda uma subconsulta para cada especialista
4. consolida as respostas: junta, aponta conflitos e devolve ao Gêmeo as
   dúvidas que só o PO pode responder
"""

from __future__ import annotations

from ..banco import slug
from ..mensagens import GEMEO, LIDER, SISTEMA, especialista
from .base import Agente, como_json
from .especialista import Especialista

PROMPT_DECOMPOR = """\
Você é o Líder do Conselho Consultivo do GigaBrain. Recebe uma consulta do Gêmeo Digital e
decide quais especialistas (um por tema de negócio) precisam responder. Um pedido pode
envolver vários temas: "notificar o cliente quando o pagamento for confirmado" envolve
pagamentos e notificações. Reaproveite especialistas existentes; só sugira tema novo
quando nenhum existente cobre o assunto. Temas são de negócio, nunca de código.

Responda com um único objeto JSON:
{
  "subconsultas": [
    {"especialista_id": "id existente ou null", "tema": "nome do tema",
     "descricao": "o que esse especialista cobre", "pergunta": "pergunta específica para ele"}
  ]
}
"""

PROMPT_CONSOLIDAR = """\
Você é o Líder do Conselho Consultivo do GigaBrain. Junte as respostas dos especialistas
em uma resposta única para o Gêmeo Digital. Aponte conflitos entre especialistas ou com
requisitos existentes, e mantenha as dúvidas que só o PO pode responder.

Responda com um único objeto JSON:
{"resumo": "...", "conflitos": ["..."], "duvidas_para_po": ["..."], "requisitos_relacionados": ["R1", ...]}
"""


class Lider(Agente):
    nome = LIDER

    def consultar(self, pergunta: str, temas: list[str]) -> dict:
        especialistas = self.ctx.banco.listar_especialistas()
        decomposicao = self.chamar_llm("lider_decompor", PROMPT_DECOMPOR, [{"role": "user", "content": como_json({
            "pergunta": pergunta,
            "temas_sugeridos": temas,
            "especialistas": [{"id": e["id"], "tema": e["tema"], "descricao": e["descricao"]} for e in especialistas],
            "modo": self.ctx.modo,
        })}])
        subconsultas = decomposicao.get("subconsultas") or [
            {"especialista_id": None, "tema": t, "descricao": f"Requisitos sobre {t}", "pergunta": pergunta} for t in temas
        ]
        self.enviar(LIDER, "decomposicao", {"pergunta": pergunta, "subconsultas": subconsultas})

        respostas, sem_especialista = [], []
        for sub in subconsultas:
            esp = self._obter_ou_criar(sub)
            if not esp:
                sem_especialista.append(sub["tema"])
                continue
            self.enviar(especialista(esp.id), "consulta", {"pergunta": sub["pergunta"], "tema": esp.tema})
            respostas.append(esp.responder(sub["pergunta"]))

        consolidada = self._consolidar(pergunta, respostas)
        if sem_especialista:
            consolidada["duvidas_para_po"].append(
                "Ainda não há especialista para: " + ", ".join(sem_especialista)
                + ". Ele será criado depois da reunião; o que você já sabe sobre isso?"
            )
        consolidada["fontes"] = [
            {"tipo": "especialista", "ref": r["especialista_id"], "versao": r["versao_conhecimento"]} for r in respostas
        ]
        self.enviar(GEMEO, "resposta_consolidada", consolidada)
        return consolidada

    def _obter_ou_criar(self, sub: dict) -> Especialista | None:
        id_ = sub.get("especialista_id") or slug(sub["tema"])
        existente = Especialista.carregar(self.ctx, id_)
        if existente:
            return existente
        if self.ctx.modo == "reuniao":
            self.enviar(SISTEMA, "pendencia", {
                "motivo": "tema sem especialista durante a reunião", "tema": sub["tema"], "descricao": sub.get("descricao"),
            })
            return None
        novo = Especialista.criar(self.ctx, sub["tema"], sub.get("descricao") or f"Requisitos sobre {sub['tema']}")
        self.enviar(SISTEMA, "especialista_criado", {"especialista_id": novo.id, "tema": novo.tema, "descricao": novo.descricao})
        return novo

    def _consolidar(self, pergunta: str, respostas: list[dict]) -> dict:
        vazio = {"resumo": "", "conflitos": [], "duvidas_para_po": [], "requisitos_relacionados": []}
        if not respostas:
            return {**vazio, "resumo": "Nenhum especialista disponível para responder."}
        if len(respostas) == 1:
            r = respostas[0]
            return {
                "resumo": r["resposta"],
                "conflitos": list(r["conflitos"]),
                "duvidas_para_po": list(r["duvidas_para_po"]),
                "requisitos_relacionados": list(r["requisitos_relacionados"]),
            }
        resultado = self.chamar_llm("lider_consolidar", PROMPT_CONSOLIDAR, [{"role": "user", "content": como_json({
            "pergunta": pergunta, "respostas": respostas,
        })}])
        return {chave: resultado.get(chave, padrao) for chave, padrao in vazio.items()}
