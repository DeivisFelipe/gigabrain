"""Arquivo de conhecimento de cada especialista.

Sem acesso ao código, o especialista não precisa de RAG: o conhecimento de um
tema cabe inteiro no prompt. Cada especialista tem um arquivo Markdown
(dados/conhecimento/<id>.md) que qualquer pessoa pode abrir, ler e corrigir.

O arquivo é a fonte da verdade. O banco guarda cada versão dele
(especialista_conhecimento), então dá para ver como o conhecimento evoluiu.
Se alguém editar o arquivo à mão, a mudança é detectada pelo hash e vira uma
versão nova com o motivo "edição manual".
"""

from __future__ import annotations

import os

# Acima disso o especialista é instruído a resumir e reorganizar o arquivo.
LIMITE_CARACTERES = 12000

# Cada requisito salvo é anotado direto no arquivo (rápido, sem LLM). A cada N
# anotações, o especialista usa o LLM para reorganizar o arquivo inteiro.
REORGANIZAR_A_CADA = 5

# Conhecimento fixo do especialista de qualidade (classe FR/NFR e subtipo).
TAXONOMIA_NFR = """\
# Especialista: qualidade (requisitos não funcionais)

> Confere se cada requisito é funcional (FR) ou não funcional (NFR) e, se NFR, o subtipo.
> Base: ISO/IEC 25010 e a taxonomia do dataset PURE/PROMISE.

## Regras de negócio
- FR: descreve um comportamento ou função do sistema (o que ele faz, entradas, saídas, regras).
- NFR: descreve uma qualidade de como o sistema faz algo (quão rápido, seguro, fácil, disponível...).
- Um requisito sobre o que o sistema faz com senhas ou dados (ex.: "a senha não tem limite de tamanho") é FR; só é NFR/SE se exigir proteção (criptografia, autenticação, controle de acesso).
- Na dúvida entre FR e NFR, prefira FR.

## Glossário
- PE — desempenho/eficiência: tempo de resposta, vazão, latência, uso de recursos.
- SE — segurança: autenticação, autorização, criptografia, privacidade, auditoria.
- US — usabilidade: facilidade de aprender e usar, ajuda, mensagens ao usuário.
- LF — aparência (look & feel): cores, fontes, layout, estilo visual.
- A — disponibilidade: uptime, 24/7, janelas de manutenção.
- SA — safety: evitar danos a pessoas, ao ambiente ou perda de dados por acidente.
- PO — portabilidade: plataformas, sistemas operacionais, navegadores, dispositivos.
- MN — manutenibilidade: modularidade, facilidade de alterar e testar.
- SC — escalabilidade: crescer em usuários, dados ou carga.
- L — legal/conformidade: leis, normas, licenças, regulamentos.
- FT — tolerância a falhas: recuperação, backup, continuar funcionando com falhas.
- OT — outra qualidade que não se encaixa acima.

## Decisões (e o porquê)
- (vazio)

## Requisitos aprovados
- (vazio)

## Pontos em aberto
- (vazio)
"""

SECOES = [
    "Regras de negócio",
    "Glossário",
    "Decisões (e o porquê)",
    "Requisitos aprovados",
    "Pontos em aberto",
]

VAZIO = "- (vazio)"


def modelo_inicial(tema: str, descricao: str) -> str:
    partes = [f"# Especialista: {tema}", "", f"> {descricao}", ""]
    for secao in SECOES:
        partes += [f"## {secao}", VAZIO, ""]
    return "\n".join(partes)


def _limites_secao(linhas: list[str], secao: str) -> tuple[int, int] | None:
    inicio = next((i for i, l in enumerate(linhas) if l.strip().lower() == f"## {secao}".lower()), None)
    if inicio is None:
        return None
    fim = next((i for i in range(inicio + 1, len(linhas)) if linhas[i].startswith("## ")), len(linhas))
    return inicio, fim


def itens(md: str, secao: str) -> list[str]:
    linhas = md.splitlines()
    limites = _limites_secao(linhas, secao)
    if not limites:
        return []
    inicio, fim = limites
    return [l[2:].strip() for l in linhas[inicio + 1:fim] if l.startswith("- ") and l.strip() != VAZIO]


def adicionar_item(md: str, secao: str, item: str) -> str:
    """Acrescenta "- item" no fim da seção (cria a seção se não existir)."""
    linhas = md.rstrip("\n").splitlines()
    limites = _limites_secao(linhas, secao)
    if not limites:
        linhas += ["", f"## {secao}", f"- {item}"]
        return "\n".join(linhas) + "\n"
    inicio, fim = limites
    corpo = [l for l in linhas[inicio + 1:fim] if l.strip() != VAZIO]
    if f"- {item}" in corpo:
        return md
    while corpo and not corpo[-1].strip():
        corpo.pop()
    corpo += [f"- {item}", ""]
    return "\n".join(linhas[:inicio + 1] + corpo + linhas[fim:]).rstrip("\n") + "\n"


def caminho(pasta: str, especialista_id: str) -> str:
    return os.path.join(pasta, f"{especialista_id}.md")


def ler(caminho_arquivo: str) -> str:
    if not os.path.exists(caminho_arquivo):
        return ""
    with open(caminho_arquivo, encoding="utf-8") as f:
        return f.read()


def escrever(caminho_arquivo: str, conteudo: str) -> None:
    os.makedirs(os.path.dirname(caminho_arquivo), exist_ok=True)
    with open(caminho_arquivo, "w", encoding="utf-8") as f:
        f.write(conteudo.rstrip("\n") + "\n")
