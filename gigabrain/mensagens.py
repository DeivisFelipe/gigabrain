"""Formato das mensagens trocadas entre os agentes.

Toda comunicação no GigaBrain passa por uma `Mensagem`: um envelope JSON com
quem enviou, para quem, o tipo da mensagem e o conteúdo estruturado. Assim
qualquer troca pode ser registrada no log, inspecionada no painel e
reproduzida depois.

Exemplo de mensagem serializada:

    {
      "id": "a1b2c3...",
      "conversa_id": "c-20261004-153000",
      "momento": "2026-10-04T15:30:02",
      "de": "lider",
      "para": "especialista:pagamentos",
      "tipo": "consulta",
      "conteudo": {"pergunta": "Existe regra para pagamentos recusados?"}
    }
"""

from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import asdict, dataclass, field

# Participantes fixos. Especialistas usam o formato "especialista:<id>".
PO = "po"
GEMEO = "gemeo"
LIDER = "lider"
SISTEMA = "sistema"
# As três bases de dados também aparecem como destinatárias das mensagens.
REPOSITORIO = "repositorio"   # Repositório de Requisitos
REGISTRO = "registro"         # Registro de Especialistas (+ arquivos de conhecimento)


def especialista(id_: str) -> str:
    return f"especialista:{id_}"


# Tipos de mensagem e o que cada uma carrega em `conteudo`.
TIPOS = {
    "pedido_po": "PO -> Gêmeo. {texto}",
    "pergunta_ao_po": "Gêmeo -> PO. {texto, duvidas?}",
    "proposta": "Gêmeo -> PO. {texto, requisitos: [...]}",
    "aprovacao": "PO -> Gêmeo. {texto}",
    "consulta": "Gêmeo -> Líder ou Líder -> Especialista. {pergunta, ...}",
    "decomposicao": "Líder -> Líder. {subconsultas: [...]}",
    "resposta_especialista": "Especialista -> Líder. {resposta, requisitos_relacionados, conflitos, duvidas_para_po, confianca}",
    "resposta_consolidada": "Líder -> Gêmeo. {resumo, conflitos, duvidas_para_po, requisitos_relacionados}",
    "especialista_criado": "Líder -> Registro. {especialista_id, tema, descricao}",
    "pendencia": "Líder -> Registro. {motivo, tema}",
    "requisito_salvo": "Gêmeo -> Repositório. {requisito_id, versao, ligacoes}",
    "requisito_em_revisao": "Repositório -> Gêmeo. {requisito_id, por_causa_de}",
    "conhecimento_atualizado": "Especialista -> Registro. {especialista_id, versao, motivo}",
    "decisao": "Gêmeo -> Gêmeo. {rascunho, salvar, motivo}: o voto final do Conselho Deliberativo",
    "transcricao": "Sistema -> Gêmeo. {projeto, titulo, turnos}",
    "fala": "Sistema -> Gêmeo. {turno, falante, texto}: a próxima fala da reunião",
    "extracao": "Gêmeo -> Gêmeo. {turno, requisitos: [rascunhos tirados dessa fala]}",
    "revisao": "Especialista -> Líder. {tema, mantidos, removidos, corrigidos, ligacoes}",
    "chamada_llm":"Agente -> LLM. {papel, duracao_ms, tokens?}",
    "erro": "Qualquer -> Sistema. {erro}",
}


def agora() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


@dataclass
class Mensagem:
    de: str
    para: str
    tipo: str
    conteudo: dict
    conversa_id: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    momento: str = field(default_factory=agora)

    def __post_init__(self) -> None:
        if self.tipo not in TIPOS:
            raise ValueError(f"tipo de mensagem desconhecido: {self.tipo}")

    def para_dict(self) -> dict:
        return asdict(self)

    def para_json(self) -> str:
        return json.dumps(self.para_dict(), ensure_ascii=False)

    @classmethod
    def de_dict(cls, dados: dict) -> Mensagem:
        return cls(**dados)
