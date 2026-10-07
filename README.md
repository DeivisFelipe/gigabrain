# GigaBrain

Conselho multiagente para **definição de requisitos de software**. O PO conversa com
um Gêmeo Digital, que consulta especialistas por tema de negócio antes de propor
requisitos. Tudo o que é aprovado vai para um repositório local com versões e
rastreabilidade.

O GigaBrain **não acessa código**. O conhecimento vem dos requisitos já aprovados,
dos documentos do projeto e do que o PO diz.

## Arquitetura

```mermaid
flowchart LR
    PO([PO real])
    subgraph Deliberativo[Conselho Deliberativo]
        G[Gêmeo Digital]
    end
    subgraph Consultivo[Conselho Consultivo]
        L[Líder]
        E1[Especialista<br/>pagamentos]
        E2[Especialista<br/>notificações]
        En[Especialista<br/>criado sob demanda]
    end
    subgraph Banco[Banco local SQLite]
        RR[(Repositório de<br/>Requisitos)]
        RE[(Registro de<br/>Especialistas)]
        LOG[(Log de<br/>mensagens)]
    end
    K[/Arquivos de conhecimento<br/>.md por especialista/]

    PO <-->|fala / pergunta / proposta| G
    G -->|consulta| L
    L -->|resposta consolidada<br/>+ dúvidas para o PO| G
    L <--> E1 & E2
    L -.->|cria se não existir| En
    L --> RE
    E1 & E2 & En --- K
    G -->|aprovado| RR
    RR -->|requisito novo dispara<br/>atualização| E1 & E2
```

### O que acontece numa conversa

1. O PO descreve o que quer.
2. O **Gêmeo** decide: perguntar ao PO, consultar o Conselho Consultivo ou propor.
3. Ao consultar, o **Líder** divide a pergunta por tema, usa os especialistas que já
   existem e cria os que faltam (no modo `reuniao` não cria; registra uma pendência).
4. Cada **especialista** responde com base no seu arquivo de conhecimento e nos
   requisitos do seu tema, e pode devolver dúvidas para o PO.
5. O Líder consolida, aponta conflitos e devolve ao Gêmeo, que leva as dúvidas ao PO
   ou faz a proposta.
6. O PO aprova (só uma aprovação explícita conta: "aprovado, mas..." não salva).
7. O requisito é salvo com fontes e ligações; quem dependia de um requisito que mudou
   fica `em_revisao`; os especialistas dos temas envolvidos atualizam o conhecimento.

### Rastreabilidade entre requisitos

Cada requisito tem versões (nenhuma é apagada) e ligações, lidas como
"origem *tipo* destino":

| Ligação | Significado | Efeito no destino |
|---|---|---|
| `refina` | detalha o destino | continua valendo |
| `divide` | é uma das partes do destino | vira `dividido` |
| `junta` | resultou da junção do destino com outros | vira `unido` |
| `substitui` | toma o lugar do destino | vira `substituido` |
| `depende_de` | precisa do destino | se o destino mudar, a origem vai para `em_revisao` |
| `conflita_com` | contradiz o destino | o PO decide |

`python main.py trilha R3` responde "como chegamos no R3?".

### Como um especialista é atualizado

O conhecimento de cada especialista é um arquivo Markdown (`dados/conhecimento/<tema>.md`)
com seções fixas: regras de negócio, glossário, decisões (e o porquê), requisitos
aprovados e pontos em aberto. Cada mudança vira uma versão no banco, com motivo e hash.
Gatilhos:

- **requisito aprovado** no tema (ou nova versão, substituição, divisão)
- **documento do projeto** entregue a ele: `python main.py alimentar pagamentos ata.md`
- **edição manual** do arquivo: detectada pelo hash na próxima consulta

Nas reuniões processadas em lote, cada requisito salvo é anotado direto no arquivo (sem
LLM); a cada 5 anotações, ou acima de ~12 mil caracteres, o especialista usa o LLM para
resumir e reorganizar o arquivo.

### Mensagens em JSON

Toda troca entre agentes é uma `Mensagem` com `de`, `para`, `tipo` e `conteudo`. Cada
mensagem vai para a tabela `evento` e para `dados/logs/<conversa>.jsonl`. Os agentes
também falam com o LLM só em JSON (entrada e saída).

## Teste com o dataset (EntradasGigabrain)

O repositório [EntradasGigabrain](https://github.com/schaumann-byte/EntradasGigabrain)
tem 18 reuniões simuladas, derivadas do dataset PURE, cada uma com um gabarito de
requisitos (classe FR/NFR, subtipo e turnos de origem). Clone-o **ao lado** deste
repositório (ou defina `GIGABRAIN_ENTRADAS`):

```
Projetos/
├── gigabrain/
└── EntradasGigabrain/
```

`python main.py avaliar <projeto|todos>` processa cada reunião **fala por fala** e compara
com o gabarito, em três modos:

- **conselho**: para cada fala, o Gêmeo rascunha os requisitos; cada rascunho vai ao
  especialista do tema (duplicados, respaldo, ligações) e ao especialista de qualidade
  (classe FR/NFR e subtipo), que só **recomendam**; o Gêmeo **decide** e grava; o
  especialista anota o requisito no seu conhecimento
- **agente_unico**: o mesmo Gêmeo, fala por fala, sem o Conselho Consultivo
- **llm_puro**: a reunião inteira num único pedido com instrução mínima

Dois cenários (`--cenario`):

- **reuniao** (padrão): uma reunião por projeto, como no dataset
- **sessoes**: cada reunião vira 3 reuniões seguidas sobre o mesmo repositório, com
  **repetições** e **mudanças** de requisitos injetadas nas reuniões seguintes. Mede se o
  sistema evita gravar de novo o que já existe e se liga a mudança ao requisito original

Com `--repeticoes N`, cada execução é repetida e o resumo traz média ± desvio e o teste
de Wilcoxon pareado por projeto entre os modos (`gigabrain/estatistica.py`). Os resultados
ficam em `dados-avaliacao/<provedor>/<cenario>/<projeto>/<modo>/rep-<n>/`, com
`resumo.csv` e `estatisticas.json` por cenário. Detalhes em [REPRODUCAO.md](REPRODUCAO.md)
e o rascunho do método em [docs/metodo-avaliacao.md](docs/metodo-avaliacao.md).

> **Atenção:** com `--simulado`, a extração é um baseline por regras (cada frase de
> stakeholder vira um requisito), não um LLM. Ele já atinge revocação ~0,98 porque as
> transcrições preservam as palavras dos requisitos originais. Ou seja, encontrar os
> requisitos é fácil neste dataset; a diferença entre os sistemas deve aparecer em
> precisão (duplicados, frases que não são requisitos), classe/subtipo e ligações.

## Como rodar

```bash
pip install -r requirements.txt

# Sem chave de API: conversas prontas com o provedor simulado
python main.py demo
python main.py painel                   # um painel para todas as pastas (seletor no topo)

# Conversa de verdade com a DeepSeek
$env:DEEPSEEK_API_KEY="sua-chave"        # PowerShell
python main.py conversar
python main.py conversar --modo reuniao  # não cria especialistas, só usa os existentes
python main.py conversar --simulado      # testar sem API

# Consultas rápidas
python main.py requisitos
python main.py trilha R3
python main.py especialistas

# Dataset de teste (EntradasGigabrain clonado ao lado)
python main.py entradas
python main.py avaliar 2008-keepass --simulado
python main.py avaliar todos --repeticoes 5            # com a DeepSeek: 3 modos, 5 repetições
python main.py avaliar todos --cenario sessoes        # várias reuniões, repetições e mudanças
python main.py anotacao exportar ligacoes.csv         # amostra para anotação manual das ligações
python main.py anotacao kappa ligacoes.csv            # concordância entre os anotadores

# Testes
python -m unittest discover tests
```

O painel (`python main.py painel`) mostra todas as pastas de dados (seletor na lateral):

- **Benchmark**: médias dos três modos, testes estatísticos e cada repetição como um ponto clicável
- **Arquitetura**: reproduz uma conversa passo a passo, destacando quem fala com quem
- **Conversas**: linha do tempo das mensagens JSON de cada conversa
- **Requisitos**: grafo de ligações, versões, fontes e a trilha de cada requisito
- **Especialistas**: o arquivo de conhecimento, cada versão e o que mudou entre elas
- **Avaliação**: comparação com o gabarito, troca de modo e repetição, e as falas injetadas

## Roteiro para ler o código

| Ordem | Arquivo | O que tem |
|---|---|---|
| 1 | `gigabrain/mensagens.py` | o envelope JSON e os tipos de mensagem |
| 2 | `gigabrain/banco.py` | as tabelas, versões, ligações e trilha |
| 3 | `gigabrain/conhecimento.py` | o arquivo de conhecimento dos especialistas |
| 4 | `gigabrain/aprovacao.py` | quando uma fala do PO conta como aprovação |
| 5 | `gigabrain/provedores.py` | DeepSeek e o simulador offline |
| 6 | `gigabrain/agentes/base.py` | o que todo agente sabe fazer (enviar, chamar o LLM) |
| 7 | `gigabrain/agentes/gemeo.py` | o Gêmeo Digital e seu prompt |
| 8 | `gigabrain/agentes/lider.py` | decomposição, criação e consolidação |
| 9 | `gigabrain/agentes/especialista.py` | responder e atualizar o conhecimento |
| 10 | `gigabrain/conselho.py` | o fluxo inteiro de uma conversa |
| 11 | `gigabrain/log.py` | para onde vai cada mensagem |
| 12 | `gigabrain/entradas.py` | leitura do dataset EntradasGigabrain |
| 13 | `gigabrain/reuniao.py` | processar uma reunião fala por fala (conselho, agente único, LLM puro) |
| 14 | `gigabrain/avaliacao.py` | comparação com o gabarito e métricas |
| 15 | `gigabrain/cenarios.py` | várias reuniões com repetições e mudanças injetadas |
| 16 | `gigabrain/estatistica.py` | média, desvio e teste de Wilcoxon pareado |
| 17 | `gigabrain/anotacao.py` | amostra de ligações para anotação manual e kappa |
| 18 | `main.py` e `gigabrain/demo.py` | linha de comando e demonstração |
| 19 | `gigabrain/painel/` | servidor e página do painel |

`arquitetura.html` e `editor.html` são a nota e o editor do diagrama original.
