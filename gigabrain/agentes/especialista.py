"""Especialista: um agente por tema de negócio (pagamentos, notificações...).

O que ele sabe está no seu arquivo de conhecimento (Markdown), mais os
requisitos aprovados do seu tema, que vêm do banco.

Ciclo de vida:
1. criar      o Líder cria quando aparece um tema novo (só fora da reunião)
2. responder  responde consultas do Líder com base no que sabe
3. atualizar  reescreve o arquivo quando algo muda. Gatilhos:
              - um requisito do seu tema foi aprovado (ou mudou de versão,
                ou foi substituído/dividido)
              - alguém entregou um documento do projeto para ele ler
              - alguém editou o arquivo à mão (detectado pelo hash)
"""

from __future__ import annotations

from .. import conhecimento
from ..banco import slug
from ..mensagens import LIDER, SISTEMA, especialista
from .base import Agente, como_json, resumo_requisito

PROMPT_RESPONDER = """\
Você é um especialista do GigaBrain no tema descrito no seu arquivo de conhecimento.
Você não tem acesso a código: só ao seu arquivo de conhecimento e aos requisitos do seu
tema. Responda à pergunta do Líder usando só esse material. Se não souber, diga que não
sabe e transforme a lacuna em pergunta para o PO.

Responda com um único objeto JSON:
{
  "resposta": "o que você sabe que importa para a pergunta",
  "requisitos_relacionados": ["R1", ...],
  "conflitos": ["descrição de cada conflito com requisito existente"],
  "duvidas_para_po": ["perguntas que só o PO pode responder"],
  "confianca": "alta" | "media" | "baixa"
}
"""

PROMPT_CRIAR = """\
Você está nascendo como especialista do GigaBrain em um tema de negócio. Monte seu arquivo
de conhecimento inicial em Markdown, seguindo exatamente as seções do modelo recebido.
Use só os requisitos recebidos como fonte; não invente regras. O que for incerto vai em
"Pontos em aberto".

Responda com um único objeto JSON: {"conhecimento_markdown": "..."}
"""

PROMPT_ATUALIZAR = """\
Você é um especialista do GigaBrain e precisa atualizar seu arquivo de conhecimento
(Markdown) por causa de um gatilho (requisito aprovado, ligação entre requisitos ou
documento do projeto). Mantenha as mesmas seções. Registre decisões com o porquê e cite os
IDs dos requisitos. Se um requisito foi substituído ou dividido, deixe isso claro. Se o
arquivo passar de limite_caracteres, resuma e reorganize sem perder decisões.

Responda com um único objeto JSON:
{"conhecimento_markdown": "...", "mudancas": ["o que mudou, em frases curtas"]}
"""


PROMPT_REVISAR = """\
Você é um especialista do GigaBrain e revisa requisitos candidatos do seu tema, extraídos
da transcrição de uma reunião. Você recebe os candidatos, as falas que cada um cita e o
seu arquivo de conhecimento. Para cada candidato decida:
- "manter":   está correto e tem respaldo nas falas citadas
- "corrigir": tem respaldo, mas o texto, a classe (FR/NFR) ou o subtipo precisam de ajuste
- "remover":  é duplicado de outro candidato (diga qual) ou não tem respaldo nas falas
Não invente requisitos e não reescreva além do necessário: mantenha as palavras originais
e o idioma da transcrição. Aponte também ligações entre candidatos do seu tema
(depende_de, conflita_com, refina), usando os índices.

Responda com um único objeto JSON:
{
  "revisao": [{"indice": 0, "acao": "manter" | "corrigir" | "remover",
               "texto": "só se corrigir", "classe": "só se corrigir", "subtipo": "só se corrigir",
               "motivo": "curto"}],
  "ligacoes": [{"origem": 3, "tipo": "depende_de", "destino": 1, "motivo": "..."}]
}
"""


class Especialista(Agente):
    def __init__(self, ctx, dados: dict):
        super().__init__(ctx)
        self.id = dados["id"]
        self.tema = dados["tema"]
        self.descricao = dados["descricao"]
        self.arquivo = dados["arquivo"]
        self.nome = especialista(self.id)

    # ------------------------------------------------------------------ criação

    @classmethod
    def criar(cls, ctx, tema: str, descricao: str, criado_por: str = LIDER) -> Especialista:
        id_ = slug(tema)
        arquivo = conhecimento.caminho(ctx.pasta_conhecimento, id_)
        dados = ctx.banco.criar_especialista(id_, tema, descricao, arquivo)
        novo = cls(ctx, dados)
        resposta = novo.chamar_llm("especialista_criar", PROMPT_CRIAR, [{"role": "user", "content": como_json({
            "tema": tema,
            "descricao": descricao,
            "modelo": conhecimento.modelo_inicial(tema, descricao),
            "requisitos_do_tema": novo.requisitos_do_tema(),
        })}])
        md = resposta.get("conhecimento_markdown") or conhecimento.modelo_inicial(tema, descricao)
        novo._gravar(md, "criação", {"criado_por": criado_por})
        return novo

    @classmethod
    def carregar(cls, ctx, id_: str) -> Especialista | None:
        dados = ctx.banco.obter_especialista(id_)
        return cls(ctx, dados) if dados else None

    # ------------------------------------------------------------------ conhecimento

    def requisitos_do_tema(self) -> list[dict]:
        return [resumo_requisito(r) for r in self.ctx.banco.listar_requisitos() if self.id in r["temas"]]

    def ler_conhecimento(self) -> str:
        return conhecimento.ler(self.arquivo)

    def _gravar(self, md: str, motivo: str, origem: dict) -> int | None:
        conhecimento.escrever(self.arquivo, md)
        versao = self.ctx.banco.registrar_conhecimento(self.id, conhecimento.ler(self.arquivo), motivo, origem)
        if versao:
            self.enviar(SISTEMA, "conhecimento_atualizado", {
                "especialista_id": self.id, "versao": versao, "motivo": motivo, **origem,
            })
        return versao

    def sincronizar(self) -> int | None:
        """Se alguém editou o arquivo à mão, guarda isso como uma versão nova."""
        md = self.ler_conhecimento()
        if not md:
            return None
        return self._gravar(md, "edição manual", {"arquivo": self.arquivo})

    def atualizar(self, gatilho: dict, motivo: str) -> int | None:
        self.sincronizar()
        resposta = self.chamar_llm("especialista_atualizar", PROMPT_ATUALIZAR, [{"role": "user", "content": como_json({
            "conhecimento_atual": self.ler_conhecimento(),
            "gatilho": gatilho,
            "requisitos_do_tema": self.requisitos_do_tema(),
            "limite_caracteres": conhecimento.LIMITE_CARACTERES,
        })}])
        md = resposta.get("conhecimento_markdown")
        if not md:
            return None
        return self._gravar(md, motivo, {"gatilho": gatilho["tipo"], "mudancas": resposta.get("mudancas", [])})

    # ------------------------------------------------------------------ revisão de transcrição

    def revisar(self, candidatos: list[dict], falas: dict[str, str]) -> dict:
        """Revisa candidatos do seu tema. Devolve {"candidatos": [...ajustados], "ligacoes": [...]}."""
        self.sincronizar()
        citadas = {t: falas[t] for c in candidatos for t in c["turnos"] if t in falas}
        resposta = self.chamar_llm("especialista_revisar", PROMPT_REVISAR, [{"role": "user", "content": como_json({
            "tema": self.tema,
            "conhecimento": self.ler_conhecimento(),
            "candidatos": [{k: c[k] for k in ("indice", "texto", "classe", "subtipo", "turnos")} for c in candidatos],
            "falas": citadas,
        })}])
        decisoes = {d.get("indice"): d for d in resposta.get("revisao", [])}
        ajustados, removidos, corrigidos = [], [], []
        for c in candidatos:
            d = decisoes.get(c["indice"], {"acao": "manter"})
            if d.get("acao") == "remover":
                removidos.append({"indice": c["indice"], "motivo": d.get("motivo")})
                continue
            novo = dict(c)
            if d.get("acao") == "corrigir":
                for campo in ("texto", "classe", "subtipo"):
                    if d.get(campo):
                        novo[campo] = d[campo]
                if novo["classe"] != "NFR":
                    novo["subtipo"] = None
                corrigidos.append({"indice": c["indice"], "motivo": d.get("motivo")})
            novo["revisado_por"] = self.id
            ajustados.append(novo)

        mantidos = {c["indice"] for c in ajustados}
        ligacoes = [l for l in resposta.get("ligacoes", []) if l.get("origem") in mantidos and l.get("destino") in mantidos]
        self.enviar(LIDER, "revisao", {
            "tema": self.tema, "mantidos": len(ajustados), "removidos": removidos, "corrigidos": corrigidos, "ligacoes": ligacoes,
        })
        return {"candidatos": ajustados, "ligacoes": ligacoes}

    # ------------------------------------------------------------------ consulta

    def responder(self, pergunta: str) -> dict:
        self.sincronizar()
        self.ctx.banco.contar_consulta(self.id)
        resposta = self.chamar_llm("especialista_responder", PROMPT_RESPONDER, [{"role": "user", "content": como_json({
            "pergunta": pergunta,
            "conhecimento": self.ler_conhecimento(),
            "requisitos_do_tema": self.requisitos_do_tema(),
        })}])
        resposta = {
            "especialista_id": self.id,
            "versao_conhecimento": self.ctx.banco.obter_especialista(self.id)["versao_conhecimento"],
            "resposta": resposta.get("resposta", ""),
            "requisitos_relacionados": resposta.get("requisitos_relacionados", []),
            "conflitos": resposta.get("conflitos", []),
            "duvidas_para_po": resposta.get("duvidas_para_po", []),
            "confianca": resposta.get("confianca", "media"),
        }
        self.enviar(LIDER, "resposta_especialista", resposta)
        return resposta
