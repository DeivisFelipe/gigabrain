"""Acesso ao LLM.

Todo agente fala com o LLM do mesmo jeito: manda um prompt de sistema e uma
lista de mensagens em que o conteúdo é JSON, e recebe JSON de volta.

    provedor.completar_json(papel="lider_decompor", sistema=..., mensagens=[...]) -> dict

Dois provedores:

- DeepSeek (ou qualquer API compatível com a da OpenAI): o de verdade.
- Simulado: não chama nenhuma API. Responde com regras fixas para cada
  papel, só para dar para rodar, testar e ver o fluxo sem gastar tokens.
"""

from __future__ import annotations

import json
import os
import re
import time

from . import conhecimento
from .banco import slug


class ErroProvedor(RuntimeError):
    pass


class ProvedorOpenAICompativel:
    def __init__(self, base_url: str, modelo: str, variavel_chave: str):
        try:
            import openai
        except ImportError as exc:
            raise ErroProvedor("Instale as dependências: pip install -r requirements.txt") from exc
        chave = os.environ.get(variavel_chave)
        if not chave:
            raise ErroProvedor(
                f"{variavel_chave} não configurada. Defina a variável de ambiente "
                "ou rode com --simulado."
            )
        self.cliente = openai.OpenAI(base_url=base_url, api_key=chave)
        self.modelo = modelo
        self.nome = f"{modelo}"
        self.ultimo_uso: dict = {}

    def completar_json(self, papel: str, sistema: str, mensagens: list[dict]) -> dict:
        conversa = [{"role": "system", "content": sistema}, *mensagens]
        for tentativa in range(2):
            resposta = self.cliente.chat.completions.create(
                model=self.modelo,
                messages=conversa,
                response_format={"type": "json_object"},
                max_tokens=8192,  # extrair requisitos de um bloco de reunião gera respostas longas
            )
            uso = resposta.usage
            self.ultimo_uso = {
                "tokens_entrada": getattr(uso, "prompt_tokens", None),
                "tokens_saida": getattr(uso, "completion_tokens", None),
            }
            texto = resposta.choices[0].message.content or ""
            try:
                return json.loads(texto)
            except json.JSONDecodeError:
                if tentativa == 1:
                    raise ErroProvedor(f"{papel}: o modelo não devolveu JSON válido: {texto[:200]}")
                conversa += [
                    {"role": "assistant", "content": texto},
                    {"role": "user", "content": "Responda apenas com um objeto JSON válido, sem texto fora dele."},
                ]
        raise AssertionError("inalcançável")


def criar_provedor(nome: str):
    if nome == "simulado":
        return ProvedorSimulado()
    if nome == "deepseek":
        return ProvedorOpenAICompativel("https://api.deepseek.com", "deepseek-chat", "DEEPSEEK_API_KEY")
    raise ErroProvedor(f"provedor desconhecido: {nome}")


# ---------------------------------------------------------------------------- simulado

PALAVRAS_TEMA = {
    "pagament": "pagamentos", "cobran": "pagamentos", "boleto": "pagamentos", "pix": "pagamentos",
    "notifica": "notificacoes", "email": "notificacoes", "e-mail": "notificacoes", "push": "notificacoes",
    "relat": "relatorios", "dashboard": "relatorios",
    "login": "contas", "cadastr": "contas", "senha": "contas",
    "estoque": "estoque", "pedido": "pedidos", "carrinho": "pedidos",
}

PALAVRAS_LIGACAO = [
    ("substitu", "substitui"), ("divid", "divide"), ("junt", "junta"), ("unir", "junta"),
    ("depend", "depende_de"), ("conflit", "conflita_com"), ("refin", "refina"), ("detalh", "refina"),
]


def _adivinhar_temas(texto: str) -> list[str]:
    t = texto.lower()
    temas = []
    for chave, tema in PALAVRAS_TEMA.items():
        if chave in t and tema not in temas:
            temas.append(tema)
    return temas or ["geral"]


HESITACOES = r"\b(uh|uhm|um|erm|you know|i mean|sort of|basically)\b,?\s*|\[(crosstalk|laughter)\]\s*"

PALAVRAS_NFR = [
    ("PE", r"response time|seconds?\b|performance|fast|latency|throughput"),
    ("SE", r"secur|encrypt|authenticat|password|permission|access control|privacy"),
    ("US", r"easy|easily|intuitive|usab|user[- ]friendly|learn"),
    ("LF", r"look|colou?r|font|layout|appearance"),
    ("A", r"availab|24 ?/ ?7|uptime"),
    ("PO", r"portab|platform|browser|operating system|windows|linux"),
    ("MN", r"maintain|modular"),
    ("SC", r"scal|concurrent users"),
    ("L", r"licen[cs]e|law|legal|regulat|complian"),
    ("FT", r"backup|recover|fault|failure"),
]


def _limpar_fala(texto: str) -> str:
    texto = re.sub(HESITACOES, "", texto, flags=re.I)
    texto = re.sub(r"\b(\w+) \1\b", r"\1", texto, flags=re.I)  # "the the" -> "the"
    return re.sub(r"\s+", " ", texto).strip()


def _classificar(frase: str) -> tuple[str, str | None]:
    for subtipo, padrao in PALAVRAS_NFR:
        if re.search(padrao, frase, re.I):
            return "NFR", subtipo
    return "FR", None


def _ultimo(mensagens: list[dict]) -> dict:
    return json.loads(mensagens[-1]["content"])


class ProvedorSimulado:
    """Imita as respostas dos agentes com regras simples (sem LLM)."""

    nome = "simulado"

    def __init__(self, atraso: float = 0.0):
        self.atraso = atraso
        self.ultimo_uso: dict = {}

    def completar_json(self, papel: str, sistema: str, mensagens: list[dict]) -> dict:
        if self.atraso:
            time.sleep(self.atraso)
        metodo = getattr(self, "_" + papel, None)
        if not metodo:
            raise ErroProvedor(f"o simulador não conhece o papel {papel}")
        return metodo(mensagens)

    # Gêmeo Digital ---------------------------------------------------------

    def _gemeo(self, mensagens: list[dict]) -> dict:
        vindas = [json.loads(m["content"]) for m in mensagens if m["role"] == "user"]
        falas_po = [m["texto"] for m in vindas if m.get("de") == "po"]
        respostas_lider = [m["resposta_consolidada"] for m in vindas if m.get("de") == "lider"]
        ultima = vindas[-1]

        if ultima.get("de") == "po" and not respostas_lider:
            return {
                "acao": "consultar",
                "mensagem_ao_po": "Vou consultar os especialistas antes de propor algo.",
                "consulta": {"pergunta": ultima["texto"], "temas": _adivinhar_temas(" ".join(falas_po))},
                "requisitos": [],
            }

        if ultima.get("de") == "lider" and ultima["resposta_consolidada"].get("duvidas_para_po"):
            rc = ultima["resposta_consolidada"]
            duvidas = "\n".join(f"- {d}" for d in rc["duvidas_para_po"])
            return {
                "acao": "perguntar",
                "mensagem_ao_po": f"Consultei os especialistas: {rc.get('resumo', '')}\n\nAntes de propor, preciso saber:\n{duvidas}",
                "consulta": None,
                "requisitos": [],
            }

        relacionados = [r for rc in respostas_lider for r in rc.get("requisitos_relacionados", [])]
        return self._proposta(falas_po, relacionados)

    def _proposta(self, falas_po: list[str], relacionados: list[str]) -> dict:
        tudo = " ".join(falas_po)
        principal = falas_po[0].strip().rstrip(".")
        # "Atualizar o R1 para notificar..." -> "notificar..."
        principal = re.sub(r"^(quero|queria|preciso|gostaria de)\s+", "", principal, flags=re.I)
        principal = re.sub(r"^(atualizar|substituir|alterar|mudar)\s+o\s+R\d+\s+(para|por)\s+", "", principal, flags=re.I)
        citados = re.findall(r"\bR\d+\b", tudo.upper())
        base = None
        if citados and re.search(r"atualiz|nova vers|alter|mud", tudo.lower()):
            base = citados[0]

        ligacoes = []
        for chave, tipo in PALAVRAS_LIGACAO:
            if chave in tudo.lower():
                alvos = [c for c in citados if c != base] or relacionados[:1]
                for alvo in alvos:
                    ligacoes.append({"tipo": tipo, "alvo": alvo, "motivo": f"PO pediu: {principal[:80]}"})
                break

        criterios = [f"Quando {f.strip().rstrip('.').lower()}, o sistema atende ao pedido." for f in falas_po[1:]]
        criterios.append("O comportamento pode ser verificado em um teste de aceite.")
        requisito = {
            "titulo": principal[:70][0].upper() + principal[1:70],
            "historia": f"Como usuário do sistema, quero {principal[0].lower() + principal[1:]}, para que o processo fique claro e verificável.",
            "criterios_aceite": criterios,
            "tipo": "negocio",
            "temas": _adivinhar_temas(tudo),
            "base": base,
            "ligacoes": ligacoes,
            "motivo": "atualização pedida pelo PO" if base else "pedido novo do PO",
        }
        return {
            "acao": "propor",
            "mensagem_ao_po": "Com base na conversa e nos especialistas, minha proposta é esta. Responda 'aprovado' para salvar ou diga o que mudar.",
            "consulta": None,
            "requisitos": [requisito],
        }

    def _gemeo_rascunhar(self, mensagens: list[dict]) -> dict:
        """Baseline por regras: cada frase da fala com cara de requisito vira um rascunho."""
        entrada = _ultimo(mensagens)
        tema = "geral"
        for linha in entrada.get("contexto", []):
            topico = re.search(r"\] Analyst: .*?(?:next topic|talk about|move on to)[:\s]+([^.?]+)", linha, re.I)
            if topico:
                tema = topico.group(1).strip().lower()
        m = re.match(r"\[(T\d+)\] (\w+): (.*)", entrada["fala"])
        if not m:
            return {"requisitos": []}
        _, falante, texto = m.groups()
        if falante == "Analyst" or re.search(r"\b(sorry|on mute|hear me|connection)\b", texto, re.I):
            return {"requisitos": []}
        requisitos = []
        for frase in re.split(r"(?<=[.!?])\s+", _limpar_fala(texto)):
            if len(frase.split()) >= 6:
                classe, subtipo = _classificar(frase)
                requisitos.append({"texto": frase, "classe": classe, "subtipo": subtipo, "tema": tema})
        return {"requisitos": requisitos}

    def _especialista_revisar(self, mensagens: list[dict]) -> dict:
        """Recomenda descartar o rascunho quase igual a um requisito já salvo do tema."""
        entrada = _ultimo(mensagens)
        from .avaliacao import similaridade
        texto = entrada["rascunho"]["texto"]
        for req in entrada.get("requisitos_do_tema", []):
            if similaridade(texto, req.get("texto") or req.get("titulo") or "") >= 0.85:
                return {"acao": "descartar", "duplicado_de": req["id"], "ligacoes": [], "motivo": f"repete o {req['id']}"}
        return {"acao": "manter", "duplicado_de": None, "ligacoes": [], "motivo": "sem duplicados no tema"}

    def _gemeo_decidir(self, mensagens: list[dict]) -> dict:
        """Segue a recomendação do especialista."""
        entrada = _ultimo(mensagens)
        rec, rascunho = entrada["recomendacao"], entrada["rascunho"]
        return {
            "salvar": rec["acao"] != "descartar",
            "texto": rec.get("texto") or rascunho["texto"],
            "classe": rec.get("classe") or rascunho["classe"],
            "subtipo": rec.get("subtipo") or rascunho["subtipo"],
            "ligacoes": rec.get("ligacoes", []),
            "motivo": f"segui a recomendação ({rec['acao']}): {rec.get('motivo', '')}",
        }

    # Líder -----------------------------------------------------------------

    def _lider_decompor(self, mensagens: list[dict]) -> dict:
        entrada = _ultimo(mensagens)
        existentes = {e["id"] for e in entrada.get("especialistas", [])}
        subconsultas = []
        for tema in entrada.get("temas_sugeridos") or ["geral"]:
            id_ = slug(tema)
            subconsultas.append({
                "especialista_id": id_ if id_ in existentes else None,
                "tema": tema,
                "descricao": f"Regras de negócio e requisitos sobre {tema}.",
                "pergunta": f"O que já se sabe sobre {tema} que afeta este pedido: {entrada['pergunta']}",
            })
        return {"subconsultas": subconsultas}

    def _lider_consolidar(self, mensagens: list[dict]) -> dict:
        entrada = _ultimo(mensagens)
        resumo, conflitos, duvidas, relacionados = [], [], [], []
        for r in entrada["respostas"]:
            resumo.append(f"[{r['especialista_id']}] {r['resposta']}")
            conflitos += [c for c in r.get("conflitos", []) if c not in conflitos]
            duvidas += [d for d in r.get("duvidas_para_po", []) if d not in duvidas]
            relacionados += [x for x in r.get("requisitos_relacionados", []) if x not in relacionados]
        return {
            "resumo": " ".join(resumo),
            "conflitos": conflitos,
            "duvidas_para_po": duvidas,
            "requisitos_relacionados": relacionados,
        }

    # Especialista ----------------------------------------------------------

    def _especialista_criar(self, mensagens: list[dict]) -> dict:
        entrada = _ultimo(mensagens)
        md = entrada["modelo"]
        for req in entrada.get("requisitos_do_tema", []):
            md = conhecimento.adicionar_item(md, "Requisitos aprovados", f"{req['id']} v{req['versao']} — {req['titulo']}")
        return {"conhecimento_markdown": md}

    def _especialista_responder(self, mensagens: list[dict]) -> dict:
        entrada = _ultimo(mensagens)
        md = entrada["conhecimento"]
        tema = md.splitlines()[0].replace("# Especialista:", "").strip() if md else "este tema"
        reqs = entrada.get("requisitos_do_tema", [])
        regras = conhecimento.itens(md, "Regras de negócio")
        abertos = conhecimento.itens(md, "Pontos em aberto")
        if reqs:
            lista = "; ".join(f"{r['id']} ({r['titulo']}, {r['status']})" for r in reqs[:4])
            resposta = f"Sobre {tema} já existem: {lista}."
        else:
            resposta = f"Ainda não há requisitos aprovados sobre {tema}."
        if regras:
            resposta += " Regras conhecidas: " + "; ".join(regras[:3]) + "."
        duvidas = list(abertos[:1])
        if not reqs:
            duvidas.append(f"Para {tema}: quem é o usuário principal e o que define que está pronto?")
        conflitos = [f"{r['id']} está em revisão e pode conflitar com o pedido." for r in reqs if r["status"] == "em_revisao"]
        return {
            "resposta": resposta,
            "requisitos_relacionados": [r["id"] for r in reqs[:3]],
            "conflitos": conflitos,
            "duvidas_para_po": duvidas,
            "confianca": "alta" if reqs else "baixa",
        }

    def _especialista_atualizar(self, mensagens: list[dict]) -> dict:
        entrada = _ultimo(mensagens)
        md = entrada["conhecimento_atual"]
        gatilho = entrada["gatilho"]
        mudancas = []
        if gatilho["tipo"] == "requisito_aprovado":
            req = gatilho["requisito"]
            md = conhecimento.adicionar_item(md, "Requisitos aprovados", f"{req['id']} v{req['versao']} — {req['titulo']}")
            md = conhecimento.adicionar_item(md, "Decisões (e o porquê)", f"{req['id']}: {req.get('motivo') or 'aprovado pelo PO'}")
            mudancas.append(f"registrei {req['id']} v{req['versao']}")
            for lig in gatilho.get("ligacoes", []):
                md = conhecimento.adicionar_item(
                    md, "Decisões (e o porquê)", f"{lig['origem']} {lig['tipo'].replace('_', ' ')} {lig['destino']}"
                )
                mudancas.append(f"ligação {lig['origem']} {lig['tipo']} {lig['destino']}")
            for rid in gatilho.get("em_revisao", []):
                md = conhecimento.adicionar_item(md, "Pontos em aberto", f"{rid} precisa ser revisto porque {req['id']} mudou")
                mudancas.append(f"{rid} em revisão")
        elif gatilho["tipo"] == "documento":
            for linha in gatilho["texto"].splitlines():
                linha = linha.strip().lstrip("-*").strip()
                if len(linha) > 15 and not linha.startswith("#"):
                    md = conhecimento.adicionar_item(md, "Regras de negócio", f"{linha} (fonte: {gatilho['nome']})")
                    mudancas.append(f"regra: {linha[:40]}")
        return {"conhecimento_markdown": md, "mudancas": mudancas}
