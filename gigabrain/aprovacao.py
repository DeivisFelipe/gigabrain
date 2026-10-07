"""Decide se uma fala do PO é uma aprovação explícita.

A versão antiga aceitava qualquer coisa começando com "aprov", então
"Aprovado, mas troca o critério 2" salvava o requisito sem a mudança. Aqui
a regra é estrita: a fala inteira precisa ser uma frase de aprovação
conhecida, e qualquer sinal de ressalva cancela.
"""

from __future__ import annotations

import re
import unicodedata

FRASES = {
    "/aprovar",
    "aprovado", "aprovada", "aprovo", "aprovar", "aprovadissimo",
    "ok aprovado", "sim aprovado", "aprovado obrigado",
    "pode fechar", "fechado", "pode salvar", "pode aprovar",
    "esta bom assim", "ta bom assim", "ta otimo assim", "esta otimo assim",
}

RESSALVAS = {"mas", "nao", "porem", "exceto", "menos", "troca", "trocar", "muda", "mudar", "altera", "alterar"}


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    if sem_acento.strip() == "/aprovar":
        return "/aprovar"
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", sem_acento).split())


def eh_aprovacao(texto: str) -> bool:
    normal = _normalizar(texto)
    if not normal:
        return False
    if set(normal.split()) & RESSALVAS:
        return False
    return normal in FRASES
