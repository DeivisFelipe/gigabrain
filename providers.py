"""Provedor de LLM para o Gêmeo Digital rodando fora do Claude Code.

O Claude só é usado de dentro do Claude Code (veja .claude/agents/gemeo-digital.md
— rode `claude --agent gemeo-digital`). Este script externo usa a API da
DeepSeek (compatível com o SDK da OpenAI); precisa de DEEPSEEK_API_KEY no
ambiente.
"""

from __future__ import annotations

import os


class DeepSeekProvider:
    """Mantém o histórico da conversa em memória, no próprio processo."""

    def __init__(self, system: str, model: str = "deepseek-chat"):
        import openai

        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY não configurada. Exporte a variável de ambiente "
                "antes de rodar main.py."
            )
        self.client = openai.OpenAI(base_url="https://api.deepseek.com", api_key=api_key)
        self.model = model
        self.system = system
        self.history: list[dict] = []

    def send(self, user_message: str) -> str:
        self.history.append({"role": "user", "content": user_message})
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": self.system}, *self.history],
        )
        reply = response.choices[0].message.content or ""
        self.history.append({"role": "assistant", "content": reply})
        return reply
