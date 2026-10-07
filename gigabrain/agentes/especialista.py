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
from ..mensagens import LIDER, REGISTRO, especialista
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


PROMPT_QUALIDADE = """\
Você é o especialista de qualidade do GigaBrain. Seu único trabalho é conferir a
classificação de um rascunho de requisito: funcional (FR) ou não funcional (NFR) e, se NFR,
o subtipo. Use as regras e o glossário do seu arquivo de conhecimento. Você só recomenda.

Responda com um único objeto JSON:
{"classe": "FR" | "NFR", "subtipo": "PE" | "SE" | "US" | "LF" | "A" | "SA" | "PO" | "MN" | "SC" | "L" | "FT" | "OT" | null,
 "motivo": "curto"}
"""

PROMPT_REVISAR = """\
Você é um especialista do GigaBrain e revisa UM rascunho de requisito do seu tema, extraído
da transcrição de uma reunião pelo Gêmeo Digital. Você recebe o rascunho, as falas que ele
cita, o seu arquivo de conhecimento, os requisitos do seu tema já salvos e requisitos
parecidos de outros temas. Você só RECOMENDA; quem decide é o Gêmeo Digital.

Recomende uma ação:
- "manter":    correto e com respaldo nas falas
- "corrigir":  tem respaldo, mas o texto, a classe (FR/NFR) ou o subtipo precisam de ajuste
- "descartar": em dois casos:
    1. DUPLICADO: diz o mesmo que um requisito já salvo, mesmo com outras palavras
       ("must" x "needs to", "the user shall" x "the system shall allow the user to")
       ou com "como eu disse antes" na frente (diga qual em "duplicado_de").
       Compare o CONTEÚDO: se não há nenhuma informação nova, é duplicado.
    2. SEM RESPALDO: as falas citadas não dizem isso.
Só é "refina" (recomende "manter" com a ligação) quando o rascunho traz informação
NOVA sobre um requisito salvo: um campo, uma condição, um limite ou um caso a mais.
Se não há duplicado nem informação nova a ligar, recomende "manter". Aponte ligações com requisitos já salvos ("alvo": "R3"):
depende_de, conflita_com, refina. Mudança de um requisito salvo (outro valor, outra regra)
é "conflita_com" ou, se o PO claramente trocou a regra, "substitui".
Não invente requisitos e mantenha as palavras originais.
IDIOMA: "texto" fica no idioma do campo "idioma" (en = inglês). NUNCA traduza; traduzir
não é uma correção.

Responda com um único objeto JSON:
{"acao": "manter" | "corrigir" | "descartar", "texto": "só se corrigir", "classe": "só se corrigir",
 "subtipo": "só se corrigir", "duplicado_de": "R3" ou null,
 "ligacoes": [{"tipo": "depende_de", "alvo": "R2", "motivo": "..."}], "motivo": "curto"}
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
    def criar_fixo(cls, ctx, id_: str, tema: str, descricao: str, md: str) -> Especialista:
        """Especialista com conhecimento pronto (ex.: a taxonomia de qualidade), sem LLM."""
        dados = ctx.banco.criar_especialista(id_, tema, descricao, conhecimento.caminho(ctx.pasta_conhecimento, id_))
        novo = cls(ctx, dados)
        novo._gravar(md, "criação", {"criado_por": "sistema", "conhecimento": "fixo"})
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
            self.enviar(REGISTRO, "conhecimento_atualizado", {
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

    def registrar_requisito(self, requisito: dict, ligacoes: list[dict], em_revisao: list[str]) -> int | None:
        """Anota um requisito salvo no arquivo, sem LLM; reorganiza com o LLM de vez em quando."""
        self.sincronizar()
        md = self.ler_conhecimento()
        md = conhecimento.adicionar_item(md, "Requisitos aprovados", f"{requisito['id']} ({requisito['classe']}) — {requisito['texto']}")
        if requisito.get("motivo"):
            md = conhecimento.adicionar_item(md, "Decisões (e o porquê)", f"{requisito['id']}: {requisito['motivo']}")
        for lig in ligacoes:
            md = conhecimento.adicionar_item(md, "Decisões (e o porquê)", f"{lig['origem']} {lig['tipo'].replace('_', ' ')} {lig['destino']}")
        for rid in em_revisao:
            md = conhecimento.adicionar_item(md, "Pontos em aberto", f"{rid} precisa ser revisto porque {requisito['id']} mudou")
        versao = self._gravar(md, f"{requisito['id']} salvo", {"gatilho": "requisito_salvo", "mudancas": [f"anotei {requisito['id']}"]})
        if versao and (versao % conhecimento.REORGANIZAR_A_CADA == 0 or len(md) > conhecimento.LIMITE_CARACTERES):
            versao = self.atualizar({"tipo": "reorganizar"}, "reorganização periódica") or versao
        return versao

    # ------------------------------------------------------------------ revisão de rascunho

    def revisar(self, rascunho: dict, falas: dict[str, str], idioma: str = "en") -> dict:
        """Recomenda o que fazer com um rascunho de requisito (não decide)."""
        self.sincronizar()
        resposta = self.chamar_llm("especialista_revisar", PROMPT_REVISAR, [{"role": "user", "content": como_json({
            "tema": self.tema,
            "idioma": idioma,
            "conhecimento": self.ler_conhecimento(),
            "requisitos_do_tema": self.requisitos_do_tema(),
            "parecidos_de_outros_temas": self.parecidos_de_outros_temas(rascunho["texto"]),
            "rascunho": {k: rascunho[k] for k in ("texto", "classe", "subtipo", "turnos")},
            "falas": falas,
        })}])
        acao = resposta.get("acao") if resposta.get("acao") in ("manter", "corrigir", "descartar") else "manter"
        sugestao = {
            "especialista_id": self.id,
            "versao_conhecimento": self.ctx.banco.obter_especialista(self.id)["versao_conhecimento"],
            "acao": acao,
            "texto": resposta.get("texto") if acao == "corrigir" else None,
            "classe": resposta.get("classe") if acao == "corrigir" else None,
            "subtipo": resposta.get("subtipo") if acao == "corrigir" else None,
            "duplicado_de": resposta.get("duplicado_de"),
            "ligacoes": [l for l in resposta.get("ligacoes", []) if isinstance(l, dict) and l.get("alvo")],
            "motivo": resposta.get("motivo", ""),
        }
        self.enviar(LIDER, "resposta_especialista", {**sugestao, "resposta": f"{acao}: {sugestao['motivo']}"})
        return sugestao

    def parecidos_de_outros_temas(self, texto: str, limite: int = 5) -> list[dict]:
        """Requisitos de outros temas com palavras em comum: duplicados podem cruzar temas."""
        from ..avaliacao import similaridade
        candidatos = []
        for req in self.ctx.banco.listar_requisitos(apenas_ativos=True):
            if self.id in req["temas"]:
                continue
            s = similaridade(texto, req["conteudo"].get("texto") or req["titulo"])
            if s >= 0.3:
                candidatos.append((s, resumo_requisito(req)))
        candidatos.sort(key=lambda c: -c[0])
        return [r for _, r in candidatos[:limite]]

    def revisar_qualidade(self, rascunho: dict) -> dict:
        """Recomenda a classe (FR/NFR) e o subtipo do rascunho."""
        resposta = self.chamar_llm("especialista_qualidade", PROMPT_QUALIDADE, [{"role": "user", "content": como_json({
            "conhecimento": self.ler_conhecimento(),
            "rascunho": {k: rascunho[k] for k in ("texto", "classe", "subtipo")},
        })}])
        classe = "NFR" if str(resposta.get("classe", rascunho["classe"])).upper() == "NFR" else "FR"
        sugestao = {"classe": classe, "subtipo": resposta.get("subtipo") if classe == "NFR" else None,
                    "motivo": resposta.get("motivo", "")}
        self.enviar(LIDER, "resposta_especialista", {
            "especialista_id": self.id, "acao": "classificar", **sugestao,
            "resposta": f"classe {classe}{' / ' + sugestao['subtipo'] if sugestao['subtipo'] else ''}: {sugestao['motivo']}",
        })
        return sugestao

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
