"""Gêmeo Digital: o agente do Conselho Deliberativo que conversa com o PO.

A cada fala do PO ele decide uma de três ações:

- perguntar: falta informação, então pergunta ao PO
- consultar: precisa saber o que já existe (requisitos, regras), então
             manda uma consulta ao Líder do Conselho Consultivo
- propor:    tem o suficiente e propõe um ou mais requisitos

O PO sempre tem a palavra final: nada é salvo sem aprovação explícita.
"""

from __future__ import annotations

from ..mensagens import GEMEO, LIDER, PO
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


PROMPT_RASCUNHAR = """\
Você é o Gêmeo Digital do GigaBrain e acompanha uma reunião de elicitação (Analyst, Client,
TechLead, EndUser) FALA POR FALA. Você recebe a fala atual de um stakeholder, as falas
anteriores como contexto e os requisitos que você já salvou nesta reunião. Rascunhe os
requisitos de software ditos NESTA fala: pode ser nenhum, um ou mais.

Regras:
- Um requisito por obrigação distinta. Fala sem requisito (contexto, cumprimento,
  problema de conexão, confirmação) -> lista vazia.
- Não rascunhe de novo algo que já está em "ja_salvos". Um detalhe novo sobre o mesmo
  assunto é outro requisito.
- IDIOMA: escreva "texto" no idioma do campo "idioma" (en = inglês). NUNCA traduza,
  mesmo que estas instruções estejam em português. Copie as palavras do stakeholder,
  tirando só hesitações (uh, erm, you know, i mean), repetições ("the the") e o início
  coloquial ("I'd like", "we'd need to be able to"). Ex.: fala "Uh, I'd like the the
  system to export reports to PDF" -> texto "The system shall export reports to PDF".
- "classe": "FR" (funcional) ou "NFR" (não funcional).
- "subtipo" só para NFR: PE (desempenho), SE (segurança), US (usabilidade),
  LF (aparência), A (disponibilidade), SA (safety), PO (portabilidade),
  MN (manutenibilidade), SC (escalabilidade), L (legal), FT (tolerância a falhas),
  OT (outro). Para FR use null.
- "tema": tema de negócio curto, em português (ex.: "banco de dados", "busca",
  "segurança"). Use o tópico que o Analyst anunciou no contexto.

Responda com um único objeto JSON:
{"requisitos": [{"texto": "...", "classe": "FR", "subtipo": null, "tema": "..."}]}
"""

# Baseline "LLM puro": um pedido mínimo, sem as regras do Gêmeo, sem conselho.
PROMPT_LLM_PURO = """\
Extract the software requirements from the meeting transcript below. Write each requirement
in the language of the transcript. For each one give its class (FR or NFR), the NFR subtype
code if NFR (PE, SE, US, LF, A, SA, PO, MN, SC, L, FT, OT; null for FR) and the ids of the
turns where it was said.

Answer with a single JSON object:
{"requisitos": [{"texto": "...", "classe": "FR", "subtipo": null, "turnos": ["T8"]}]}
"""

FALAS_DE_CONTEXTO = 6
SALVOS_NO_PROMPT = 30

PROMPT_DECIDIR = """\
Você é o Gêmeo Digital, representante do PO no Conselho Deliberativo do GigaBrain, e tem o
voto final sobre o que entra no Repositório de Requisitos. O especialista do tema revisou o
seu rascunho e fez uma recomendação. Decida:
- salvar o requisito (aplicando ou não as correções e ligações sugeridas), ou
- descartá-lo (por exemplo, se repete um requisito já salvo ou não tem respaldo nas falas).
Siga a recomendação quando ela se apoia nas falas; rejeite-a quando contraria o que foi dito.
Só descarte por duplicado se o rascunho diz exatamente a mesma obrigação de um requisito
salvo; se acrescenta ou detalha algo, salve e ligue com "refina".
IDIOMA: "texto" fica no idioma do campo "idioma" (en = inglês). NUNCA traduza.

Responda com um único objeto JSON:
{"salvar": true, "texto": "...", "classe": "FR" | "NFR", "subtipo": null,
 "ligacoes": [{"tipo": "depende_de", "alvo": "R2", "motivo": "..."}], "motivo": "por que decidiu assim"}
"""


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

    def rascunhar(self, projeto: dict, indice_fala: int, ja_salvos: list[dict], proximo_indice: int) -> list[dict]:
        """Lê UMA fala da reunião (com as anteriores como contexto) e devolve os rascunhos dela."""
        turnos = projeto["turnos"]
        fala = turnos[indice_fala]
        linha = lambda t: f"[{t['id']}] {t['falante']}: {t['texto']}"
        resposta = self.chamar_llm("gemeo_rascunhar", PROMPT_RASCUNHAR, [{"role": "user", "content": como_json({
            "projeto": projeto["titulo"],
            "idioma": projeto.get("idioma", "en"),
            "contexto": [linha(t) for t in turnos[max(0, indice_fala - FALAS_DE_CONTEXTO):indice_fala]],
            "fala": linha(fala),
            "ja_salvos": ja_salvos[-SALVOS_NO_PROMPT:],
        })}])
        rascunhos = []
        for req in resposta.get("requisitos", []):
            if not req.get("texto"):
                continue
            nfr = str(req.get("classe", "")).upper() == "NFR"
            rascunhos.append({
                "indice": proximo_indice + len(rascunhos),
                "texto": req["texto"].strip(),
                "classe": "NFR" if nfr else "FR",
                "subtipo": req.get("subtipo") if nfr else None,
                "tema": req.get("tema") or "geral",
                "turnos": [fala["id"]],
            })
        if rascunhos:
            self.enviar(GEMEO, "extracao", {"turno": fala["id"], "requisitos": rascunhos})
        return rascunhos

    def extrair_tudo_de_uma_vez(self, projeto: dict) -> list[dict]:
        """Baseline "LLM puro": a reunião inteira num pedido só, com o prompt mínimo."""
        resposta = self.chamar_llm("llm_puro", PROMPT_LLM_PURO, [{"role": "user", "content": "\n".join(
            f"[{t['id']}] {t['falante']}: {t['texto']}" for t in projeto["turnos"])}])
        rascunhos = []
        for req in resposta.get("requisitos", []):
            if not req.get("texto"):
                continue
            nfr = str(req.get("classe", "")).upper() == "NFR"
            rascunhos.append({"indice": len(rascunhos), "texto": req["texto"].strip(), "classe": "NFR" if nfr else "FR",
                              "subtipo": req.get("subtipo") if nfr else None, "tema": "geral",
                              "turnos": [t for t in req.get("turnos", []) if isinstance(t, str)]})
        self.enviar(GEMEO, "extracao", {"requisitos": rascunhos})
        return rascunhos

    def decidir(self, rascunho: dict, sugestao: dict, falas: dict[str, str], idioma: str = "en") -> dict:
        """Voto final: salvar (como está ou corrigido) ou descartar o rascunho."""
        if sugestao["acao"] == "manter" and not sugestao.get("ligacoes"):
            decisao = {"salvar": True, "texto": rascunho["texto"], "classe": rascunho["classe"],
                       "subtipo": rascunho["subtipo"], "ligacoes": [], "motivo": "especialista concordou com o rascunho"}
        else:
            resposta = self.chamar_llm("gemeo_decidir", PROMPT_DECIDIR, [{"role": "user", "content": como_json({
                "idioma": idioma,
                "rascunho": {k: rascunho[k] for k in ("texto", "classe", "subtipo", "turnos")},
                "recomendacao": sugestao,
                "falas": falas,
            })}])
            classe = "NFR" if str(resposta.get("classe", rascunho["classe"])).upper() == "NFR" else "FR"
            decisao = {
                "salvar": bool(resposta.get("salvar", True)),
                "texto": (resposta.get("texto") or rascunho["texto"]).strip(),
                "classe": classe,
                "subtipo": resposta.get("subtipo") if classe == "NFR" else None,
                "ligacoes": [l for l in resposta.get("ligacoes", []) if isinstance(l, dict) and l.get("alvo")],
                "motivo": resposta.get("motivo", ""),
            }
        self.enviar(GEMEO, "decisao", {
            "rascunho": rascunho["indice"], "salvar": decisao["salvar"], "recomendacao": sugestao["acao"],
            "texto": ("salvar: " if decisao["salvar"] else "descartar: ") + decisao["motivo"],
        })
        return decisao
