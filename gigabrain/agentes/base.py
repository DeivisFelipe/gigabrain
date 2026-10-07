"""Peças comuns a todos os agentes.

`Contexto` junta o que todo agente precisa: o banco, o registro de log, o
provedor de LLM, onde ficam os arquivos de conhecimento, o modo da conversa e
o id da conversa atual.

`Agente` dá a cada agente dois atalhos:
- enviar(...)     cria uma Mensagem JSON e registra no log
- chamar_llm(...) chama o LLM e registra quanto tempo levou
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from ..banco import Banco
from ..log import Registro
from ..mensagens import SISTEMA, Mensagem

MODOS = {
    "reuniao": "durante a reunião: rápido, só usa especialistas que já existem",
    "pos_reuniao": "depois da reunião: pode criar especialistas novos",
}


@dataclass
class Contexto:
    banco: Banco
    registro: Registro
    provedor: object
    pasta_conhecimento: str
    modo: str = "pos_reuniao"
    conversa_id: str | None = None


def resumo_requisito(req: dict) -> dict:
    """Forma enxuta de um requisito para mandar aos agentes."""
    c = req["conteudo"]
    return {
        "id": req["id"],
        "versao": req["versao_atual"],
        "status": req["status"],
        "titulo": c.get("titulo"),
        "texto": c.get("texto"),
        "historia": c.get("historia"),
        "criterios_aceite": c.get("criterios_aceite", []),
        "temas": req["temas"],
    }


def como_json(dados: dict) -> str:
    return json.dumps(dados, ensure_ascii=False, indent=1)


class Agente:
    nome = "agente"

    def __init__(self, ctx: Contexto):
        self.ctx = ctx

    def enviar(self, para: str, tipo: str, conteudo: dict) -> Mensagem:
        msg = Mensagem(de=self.nome, para=para, tipo=tipo, conteudo=conteudo, conversa_id=self.ctx.conversa_id)
        return self.ctx.registro.registrar(msg)

    def chamar_llm(self, papel: str, sistema: str, mensagens: list[dict]) -> dict:
        inicio = time.perf_counter()
        try:
            resposta = self.ctx.provedor.completar_json(papel, sistema, mensagens)
        except Exception as exc:
            self.enviar(SISTEMA, "erro", {"papel": papel, "erro": str(exc)})
            raise
        duracao = round((time.perf_counter() - inicio) * 1000)
        self.enviar("llm", "chamada_llm", {
            "papel": papel,
            "provedor": getattr(self.ctx.provedor, "nome", "?"),
            "duracao_ms": duracao,
            **getattr(self.ctx.provedor, "ultimo_uso", {}),
        })
        return resposta
