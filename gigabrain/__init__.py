"""GigaBrain: multi-agent council for software requirements.

Modules, in a good reading order:

- messages.py            the JSON envelope every agent uses
- database.py            local SQLite databases: requirements, experts and log
- requirement_format.py  requirement structure (EARS + user story + Gherkin) and quality checks
- knowledge.py           knowledge file of each expert
- approval.py            when a PO message counts as an approval
- providers.py           access to the LLM (DeepSeek) or an offline simulator
- log.py                 records every message
- agents/                Digital Twin, Leader, Experts and the Critic
- validation/            what the Critic scores with: the checks, the gate and the report (no LLM)
- council.py             a whole conversation with the PO
- meeting.py             a meeting processed one utterance at a time
- api.py                 REST API for the web application
- dashboard/             visual dashboard
- dataset.py, evaluation.py, scenarios.py, stats.py, annotation.py   evaluation with the test dataset
"""
