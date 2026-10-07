"""Registro de tudo o que os agentes fazem.

Cada `Mensagem` enviada passa por aqui e vai para três lugares:

1. tabela `evento` do banco      -> consultas e painel
2. dados/logs/<conversa>.jsonl   -> uma linha JSON por mensagem, fácil de abrir
3. terminal (se ver_agentes=True) -> acompanhar a conversa interna ao vivo
"""

from __future__ import annotations

import json
import os

from .banco import Banco
from .mensagens import Mensagem

CORES = {
    "po": "\033[97m",
    "gemeo": "\033[96m",
    "lider": "\033[95m",
    "especialista": "\033[93m",
    "sistema": "\033[90m",
}
RESET = "\033[0m"


def _cor(participante: str) -> str:
    return CORES.get(participante.split(":")[0], "")


def resumo(msg: Mensagem) -> str:
    """Uma linha legível sobre o que a mensagem diz."""
    c = msg.conteudo
    if msg.tipo == "decomposicao":
        return "temas: " + ", ".join(f"{s.get('tema')}" for s in c.get("subconsultas", []))
    if msg.tipo == "especialista_criado":
        return f"novo especialista '{c.get('especialista_id')}'"
    if msg.tipo == "pendencia":
        return f"{c.get('motivo')} ({c.get('tema')})"
    if msg.tipo == "conhecimento_atualizado":
        return f"{c.get('especialista_id')} v{c.get('versao')}: {c.get('motivo')}"
    for chave in ("texto", "pergunta", "resumo", "resposta", "motivo", "erro"):
        if c.get(chave):
            texto = str(c[chave]).replace("\n", " ")
            return texto[:110] + ("…" if len(texto) > 110 else "")
    if msg.tipo == "chamada_llm":
        return f"{c.get('papel')} em {c.get('duracao_ms')} ms"
    if msg.tipo == "requisito_salvo":
        return f"{c.get('requisito_id')} v{c.get('versao')}"
    return json.dumps(c, ensure_ascii=False)[:110]


class Registro:
    def __init__(self, banco: Banco, pasta_logs: str, ver_agentes: bool = False):
        self.banco = banco
        self.pasta_logs = pasta_logs
        self.ver_agentes = ver_agentes
        os.makedirs(pasta_logs, exist_ok=True)

    def registrar(self, msg: Mensagem) -> Mensagem:
        self.banco.registrar_evento(msg)
        nome = msg.conversa_id or "sem-conversa"
        with open(os.path.join(self.pasta_logs, f"{nome}.jsonl"), "a", encoding="utf-8") as f:
            f.write(msg.para_json() + "\n")
        if self.ver_agentes and msg.tipo not in ("pedido_po", "aprovacao"):
            print(f"  {_cor(msg.de)}[{msg.de} → {msg.para}]{RESET} {msg.tipo}: {resumo(msg)}")
        return msg
