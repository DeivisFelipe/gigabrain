# Como reproduzir os resultados

Este guia permite que qualquer pessoa (inclusive revisores) rode de novo toda a
avaliação do GigaBrain e chegue às mesmas tabelas.

## 1. Ambiente

- Python 3.12 (testado no Windows 11; não usa nada específico de sistema operacional)
- Dependência única: `openai` (cliente compatível com a API da DeepSeek)

```bash
git clone https://github.com/DeivisFelipe/gigabrain
git clone https://github.com/schaumann-byte/EntradasGigabrain   # dataset, ao lado do gigabrain
cd gigabrain
pip install -r requirements.txt
python -m unittest discover tests                                # 23 testes, offline
```

Estrutura esperada:

```
Projetos/
├── gigabrain/
└── EntradasGigabrain/       (ou defina GIGABRAIN_ENTRADAS com o caminho)
```

## 2. Modelo

- Provedor: DeepSeek, modelo `deepseek-chat`, via API compatível com a da OpenAI
- `response_format = json_object`, `max_tokens = 8192`, temperatura padrão do provedor
- Variável de ambiente: `DEEPSEEK_API_KEY`

O LLM não é determinístico. Por isso cada configuração é repetida (`--repeticoes`)
e os resultados são relatados como média ± desvio entre repetições.

## 3. Execuções do artigo

```bash
# QP1: qualidade de extração, uma reunião por projeto, 3 modos x 5 repetições
python main.py avaliar todos --repeticoes 5

# QP2: várias reuniões por projeto, com repetições e mudanças injetadas
python main.py avaliar todos --cenario sessoes --repeticoes 5

# Ablação: conselho sem o especialista de qualidade
python main.py avaliar todos --modo conselho --sem-qualidade --repeticoes 5
```

Opções úteis: `--paralelo N` (execuções ao mesmo tempo, padrão 4) e `--modo`
(`conselho`, `agente_unico`, `llm_puro`, ou vários separados por vírgula).
Sem chave de API, acrescente `--simulado` para validar o pipeline (baseline por regras).

## 4. Onde ficam os resultados

```
dados-avaliacao/<provedor>/<cenario>/
├── resumo.csv              uma linha por (projeto, modo, repetição)
├── estatisticas.json       médias por modo e testes de Wilcoxon pareados
└── <projeto>/<modo>/rep-<n>/
    ├── avaliacao.json      métricas, pares com o gabarito, falsos positivos, injeções
    ├── requisitos.db       Repositório de Requisitos produzido
    ├── especialistas.db    Registro de Especialistas (com todas as versões do conhecimento)
    ├── log.db + logs/      todas as mensagens JSON trocadas, incluindo as chamadas ao LLM
    └── conhecimento/       arquivo Markdown de cada especialista
```

Para inspecionar visualmente: `python main.py painel` (abre em http://127.0.0.1:8765/).

## 5. Anotação manual das ligações (QP3)

```bash
python main.py anotacao exportar ligacoes.csv --amostra 60     # sorteio com semente fixa
# duas pessoas preenchem as colunas anotador_1 e anotador_2 com c (correta) ou i (incorreta)
python main.py anotacao kappa ligacoes.csv                      # concordância e precisão das ligações
```

## 6. Parâmetros fixos

| Parâmetro | Valor | Onde |
|---|---|---|
| Pareamento com o gabarito | Dice de palavras de conteúdo ≥ 0,5, um para um | `gigabrain/avaliacao.py` |
| "Sem respaldo" | cobertura das palavras pela fala citada < 0,3 | `gigabrain/avaliacao.py` |
| Falas de contexto por rascunho | 6 | `gigabrain/agentes/gemeo.py` |
| Reorganização do conhecimento | a cada 5 anotações ou acima de 12 000 caracteres | `gigabrain/conhecimento.py` |
| Cenário com várias reuniões | 3 sessões, 15% repetições + 15% mudanças, semente 42 | `gigabrain/cenarios.py` |
| Teste estatístico | Wilcoxon pareado por projeto (p exato até 25 projetos), efeito rank-biserial | `gigabrain/estatistica.py` |
