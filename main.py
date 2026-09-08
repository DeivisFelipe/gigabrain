"""Conselho Deliberativo (via DeepSeek) — loop de conversa entre PO Real e Gêmeo Digital.

Este script roda o Gêmeo Digital fora do Claude Code, usando a API da DeepSeek.
Para usar o Claude, rode direto no Claude Code: `claude --agent gemeo-digital`
(veja .claude/agents/gemeo-digital.md).

Uso:
    export DEEPSEEK_API_KEY=sua-chave
    python main.py
"""

from __future__ import annotations

import datetime
import os

from gemeo_digital import SYSTEM_PROMPT, looks_like_approval
from providers import DeepSeekProvider

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "requisitos")


def save_requirement(text: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(OUTPUT_DIR, f"requisito-{stamp}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n")
    return path


def main() -> None:
    provider = DeepSeekProvider(SYSTEM_PROMPT)
    pending_proposal: str | None = None

    print("=== Conselho Deliberativo (provedor: deepseek) ===")
    print("Descreva o que você quer. Quando o Gêmeo Digital propuser um requisito,")
    print("responda 'aprovado' para fechar, ou continue dando feedback para refinar.")
    print("Digite 'sair' para encerrar sem aprovar.\n")

    while True:
        try:
            user_input = input("PO Real> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nEncerrando sem requisito definido.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("sair", "exit", "quit"):
            print("Encerrando sem requisito definido.")
            break

        if pending_proposal and looks_like_approval(user_input):
            path = save_requirement(pending_proposal)
            print(f"\n✅ Requisito aprovado! Salvo em: {path}\n")
            break

        try:
            reply = provider.send(user_input)
        except Exception as exc:  # falha de API — não derruba a conversa
            print(f"\n[erro ao chamar a DeepSeek: {exc}]\n")
            continue

        print(f"\nGêmeo Digital> {reply}\n")
        pending_proposal = reply if "## requisito proposto" in reply.lower() else None


if __name__ == "__main__":
    main()
