"""GigaBrain: conselho multiagente para definição de requisitos.

Visão geral dos módulos (na ordem em que vale a pena ler):

- mensagens.py   — o formato JSON que todos os agentes usam para conversar
- banco.py       — banco SQLite local: requisitos, especialistas e eventos
- log.py         — registra cada mensagem trocada (banco + arquivo .jsonl)
- provedores.py  — acesso ao LLM (DeepSeek) ou um simulador offline
- conhecimento.py— arquivo de conhecimento de cada especialista
- agentes/       — Gêmeo Digital, Líder e Especialistas
- conselho.py    — orquestra uma conversa inteira com o PO
- painel/        — painel web para ver tudo de forma visual
"""
