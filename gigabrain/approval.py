"""Decide whether something the PO wrote is an explicit approval.

The rule is strict: the whole message must be a known approval phrase, and any sign
of reservation cancels it ("approved, but change the deadline" is not an approval).
The PO may write in English or Portuguese, so both phrase lists are kept.
"""

from __future__ import annotations

import re
import unicodedata

PHRASES = {
    "/approve", "/aprovar",
    # English
    "approved", "approve", "i approve", "ok approved", "yes approved", "looks good", "lgtm",
    "save it", "go ahead", "ship it", "its good", "it is good", "all good",
    # Portuguese
    "aprovado", "aprovada", "aprovo", "aprovar", "ok aprovado", "sim aprovado", "aprovado obrigado",
    "pode fechar", "fechado", "pode salvar", "pode aprovar", "esta bom assim", "ta bom assim",
    "ta otimo assim", "esta otimo assim",
}

RESERVATIONS = {
    # English
    "but", "not", "no", "except", "change", "however", "instead", "dont",
    # Portuguese
    "mas", "nao", "porem", "exceto", "menos", "troca", "trocar", "muda", "mudar", "altera", "alterar",
}


def _normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()
    if plain in ("/approve", "/aprovar"):
        return plain
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", plain.replace("'", "")).split())


def is_approval(text: str) -> bool:
    normal = _normalize(text)
    if not normal:
        return False
    if set(normal.split()) & RESERVATIONS:
        return False
    return normal in PHRASES
