"""Gêmeo Digital: o agente do Conselho Deliberativo que conversa com o PO.

A cada fala do PO ele decide uma de três ações:

- perguntar: falta informação, então pergunta ao PO
- consultar: precisa saber o que já existe (requisitos, regras), então
             manda uma consulta ao Líder do Conselho Consultivo
- propor:    tem o suficiente e propõe um ou mais requisitos

O PO sempre tem a palavra final: nada é salvo sem aprovação explícita.
"""

from __future__ import annotations

from ..mensagens import LIDER, PO
from .base import Agente, como_json

SYSTEM_PROMPT = """\
Você é o Gêmeo Digital do Product Owner (PO) no sistema GigaBrain, especializado em
elicitação e definição de requisitos de software. Você conversa com o PO real para
transformar um pedido, muitas vezes vago, em requisitos bem definidos.

O GigaBrain NÃO acessa código. Todo o conhecimento sobre o produto vem dos requisitos já
aprovados, dos documentos do projeto e do que o PO diz. Esse conhecimento fica com o
Conselho Consultivo (o Líder e os especialistas por tema de negócio). Você consulta o
Líder quando precisa saber o que já foi decidido, se o pedido parece com algo existente,
ou se pode conflitar com outro requisito. Não invente regras do produto.

Boas práticas que você aplica:
- INVEST: Independente, Negociável, Valioso, Estimável, Small (pequeno) e Testável.
- História no formato "Como <persona>, quero <ação>, para que <benefício>".
- Critérios de aceite objetivos e verificáveis ("quando X, o sistema faz Y"), cobrindo o
  caminho feliz e as exceções óbvias.
- Sinalize ao PO: solução técnica disfarçada de requisito, palavras vagas sem critério
  ("rápido", "intuitivo") e escopo grande demais num pedido só (sugira dividir).
- Enablers técnicos óbvios entram como requisito separado com "tipo": "enabler".

Rastreabilidade: quando o pedido muda, detalha ou contradiz um requisito existente,
diga isso na proposta:
- "base": "R3" quando o pedido é uma nova versão do R3 (mesmo requisito, conteúdo novo)
- "ligacoes": relações do requisito novo com outros, lidas como "novo <tipo> alvo":
  refina, divide, junta, substitui, depende_de, conflita_com

Você recebe mensagens JSON:
- {"de": "po", "texto": "..."}                          fala do PO
- {"de": "lider", "resposta_consolidada": {...}}        resposta do Conselho Consultivo
  (resumo, conflitos, duvidas_para_po, requisitos_relacionados)
- {"de": "sistema", "aviso": "..."}                     aviso do sistema

Se o Líder devolver duvidas_para_po, leve essas dúvidas ao PO antes de propor.
Se houver conflitos, mostre ao PO e deixe ele decidir.

Responda SEMPRE com um único objeto JSON:
{
  "acao": "perguntar" | "consultar" | "propor",
  "mensagem_ao_po": "texto curto e direto para o PO, em português",
  "consulta": {"pergunta": "...", "temas": ["pagamentos", ...]} ou null,
  "requisitos": [
    {
      "titulo": "...",
      "historia": "Como ..., quero ..., para que ...",
      "criterios_aceite": ["...", "..."],
      "tipo": "negocio" | "enabler",
      "temas": ["pagamentos"],
      "base": null ou "R<n>",
      "ligacoes": [{"tipo": "refina", "alvo": "R<n>", "motivo": "..."}],
      "motivo": "por que este requisito (ou esta nova versão) existe"
    }
  ]
}
"consulta" só vem preenchida com "acao": "consultar"; "requisitos" só com "acao": "propor".
Seja direto e conciso. Não repita o que o PO já disse.
"""


PROMPT_EXTRAIR = """\
Você é o Gêmeo Digital do GigaBrain. Recebe um trecho da transcrição de uma reunião de
elicitação (Analyst, Client, TechLead, EndUser) e extrai os requisitos de software ditos
pelos stakeholders.

Regras:
- Um requisito por obrigação distinta. Uma fala pode conter mais de um requisito.
- O Analyst só conduz e confirma ("Just to confirm, ..."): não duplique o que ele repete.
- Ignore falas fora do tópico (problemas de conexão, microfone, risadas).
- Escreva cada requisito no MESMO idioma da transcrição, como frase declarativa, mantendo
  as palavras originais e tirando só hesitações (uh, erm, you know, i mean) e repetições.
- "classe": "FR" (funcional) ou "NFR" (não funcional).
- "subtipo" só para NFR: PE (desempenho), SE (segurança), US (usabilidade),
  LF (aparência), A (disponibilidade), SA (safety), PO (portabilidade),
  MN (manutenibilidade), SC (escalabilidade), L (legal), FT (tolerância a falhas),
  OT (outro). Para FR use null.
- "tema": tema de negócio curto, em português, no singular ou plural consistente
  (ex.: "banco de dados", "busca", "segurança"). Use o tópico que o Analyst anunciou.
- "turnos": os ids dos turnos onde o requisito foi dito (ex.: ["T8"]), sem os de contexto.
- Os primeiros turnos do trecho podem ser só contexto do bloco anterior: extraia apenas
  requisitos ditos a partir do turno "extrair_a_partir_de".

Responda com um único objeto JSON:
{"requisitos": [{"texto": "...", "classe": "FR", "subtipo": null, "tema": "...", "turnos": ["T8"]}]}
"""

TURNOS_POR_BLOCO = 40


class GemeoDigital(Agente):
    nome = "gemeo"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.historico: list[dict] = []

    def _decidir(self, entrada: dict) -> dict:
        self.historico.append({"role": "user", "content": como_json(entrada)})
        decisao = self.chamar_llm("gemeo", SYSTEM_PROMPT, self.historico)
        decisao.setdefault("requisitos", [])
        if decisao.get("acao") not in ("perguntar", "consultar", "propor"):
            decisao["acao"] = "perguntar"
        if decisao["acao"] == "propor" and not decisao["requisitos"]:
            decisao["acao"] = "perguntar"
        self.historico.append({"role": "assistant", "content": como_json(decisao)})
        self._anunciar(decisao)
        return decisao

    def _anunciar(self, decisao: dict) -> None:
        if decisao["acao"] == "consultar":
            self.enviar(LIDER, "consulta", decisao["consulta"])
        elif decisao["acao"] == "propor":
            self.enviar(PO, "proposta", {"texto": decisao.get("mensagem_ao_po", ""), "requisitos": decisao["requisitos"]})
        else:
            self.enviar(PO, "pergunta_ao_po", {"texto": decisao.get("mensagem_ao_po", "")})

    def ouvir_po(self, texto: str) -> dict:
        return self._decidir({"de": "po", "texto": texto})

    def ouvir_lider(self, resposta_consolidada: dict) -> dict:
        return self._decidir({"de": "lider", "resposta_consolidada": resposta_consolidada})

    def ouvir_sistema(self, aviso: str) -> dict:
        return self._decidir({"de": "sistema", "aviso": aviso})

    def extrair_da_transcricao(self, projeto: dict) -> list[dict]:
        """Lê a reunião em blocos de turnos e devolve os requisitos candidatos."""
        turnos = projeto["turnos"]
        candidatos: list[dict] = []
        for inicio in range(0, len(turnos), TURNOS_POR_BLOCO):
            # Repete os 2 turnos anteriores para não perder o tópico anunciado pelo Analyst.
            bloco = turnos[max(0, inicio - 2):inicio + TURNOS_POR_BLOCO]
            resposta = self.chamar_llm("gemeo_extrair", PROMPT_EXTRAIR, [{"role": "user", "content": como_json({
                "projeto": projeto["titulo"],
                "extrair_a_partir_de": turnos[inicio]["id"],
                "transcricao": "\n".join(f"[{t['id']}] {t['falante']}: {t['texto']}" for t in bloco),
            })}])
            for req in resposta.get("requisitos", []):
                if not req.get("texto"):
                    continue
                candidatos.append({
                    "indice": len(candidatos),
                    "texto": req["texto"].strip(),
                    "classe": "NFR" if str(req.get("classe", "")).upper() == "NFR" else "FR",
                    "subtipo": req.get("subtipo") if str(req.get("classe", "")).upper() == "NFR" else None,
                    "tema": req.get("tema") or "geral",
                    "turnos": [t for t in req.get("turnos", []) if isinstance(t, str)],
                })
        self.enviar(LIDER, "extracao", {"projeto": projeto["id"], "requisitos": candidatos})
        return candidatos
