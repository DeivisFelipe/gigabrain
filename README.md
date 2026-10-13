# GigaBrain

Sistema multiagente para definir requisitos de software. O PO conversa com um agente (o
Gêmeo Digital), que consulta especialistas antes de propor requisitos. O que o PO aprova
vai para um banco local, com versões e ligações entre requisitos.

O sistema não lê código. Ele trabalha só com requisitos, documentos do projeto e o que o PO diz.

## Instalação

```bash
git clone https://github.com/DeivisFelipe/gigabrain
cd gigabrain
pip install -r requirements.txt
```

Para usar o modelo de verdade, defina a chave da DeepSeek:

```bash
$env:DEEPSEEK_API_KEY="sua-chave"      # PowerShell
export DEEPSEEK_API_KEY="sua-chave"    # Linux/Mac
```

Sem chave, quase todos os comandos aceitam `--simulado`, que responde por regras fixas.
Serve para testar o fluxo, não para medir qualidade.

## Comandos

| Comando | O que faz |
|---|---|
| `python main.py conversar` | conversa com o Gêmeo no terminal |
| `python main.py api` | sobe a API REST para a aplicação web |
| `python main.py painel` | abre o painel visual no navegador |
| `python main.py demo` | roda 5 conversas de exemplo (simulado) |
| `python main.py requisitos` | lista os requisitos salvos |
| `python main.py trilha R3` | mostra como se chegou no R3 |
| `python main.py especialistas` | lista os especialistas |
| `python main.py alimentar pagamentos ata.md` | entrega um documento a um especialista |
| `python main.py avaliar ...` | avaliação com o dataset (ver abaixo) |
| `python main.py anotacao ...` | anotação manual das ligações (ver abaixo) |
| `python -m unittest discover tests` | testes (offline) |

A opção `--dados <pasta>` (antes do comando) escolhe onde ficam os bancos. Padrão: `dados/`.

## Como funciona

```
PO ──▶ Gêmeo Digital ──▶ Líder ──▶ Especialistas (um por tema: pagamentos, busca...)
        ▲                  │
        └── recomendações ─┘
        │
        └─▶ PO aprova ──▶ Repositório de Requisitos
```

- **Gêmeo Digital** (Conselho Deliberativo): conversa com o PO, pergunta, propõe requisitos.
- **Líder** (Conselho Consultivo): leva a pergunta ao especialista certo. Cria especialistas
  novos quando aparece um tema novo.
- **Especialistas**: cada um cuida de um tema e guarda o que sabe num arquivo Markdown.
  Eles só recomendam.
- **Quem decide:** o PO, na conversa. Nas reuniões processadas em lote (sem PO), quem decide
  é o Gêmeo.
- **Especialista de qualidade:** fixo, confere se o requisito é funcional (FR) ou não
  funcional (NFR) e o subtipo (desempenho, segurança...).

Nada é salvo sem aprovação explícita. "Aprovado, mas troca o prazo" não aprova.

### Requisitos

Cada requisito tem um id (R1, R2...), versões (nenhuma é apagada) e ligações com outros:

| Ligação | Significado | Efeito no outro requisito |
|---|---|---|
| `refina` | detalha | nenhum |
| `divide` | é uma parte | fica `dividido` |
| `junta` | juntou com outros | fica `unido` |
| `substitui` | toma o lugar | fica `substituido` |
| `depende_de` | precisa dele | se ele mudar, este fica `em_revisao` |
| `conflita_com` | contradiz | o PO decide |

Lê-se "origem *ligação* destino": "R3 substitui R1".

### Especialistas

O conhecimento de cada especialista fica em `dados/conhecimento/<tema>.md`, com seções fixas:
regras de negócio, glossário, decisões, requisitos aprovados e pontos em aberto. Toda
mudança vira uma versão nova no banco, com o motivo. O arquivo muda quando:

- um requisito do tema é aprovado;
- alguém entrega um documento (`python main.py alimentar <especialista> <arquivo>`);
- alguém edita o arquivo à mão.

### Bancos

Três arquivos SQLite na pasta de dados:

| Arquivo | Conteúdo |
|---|---|
| `requisitos.db` | requisitos, versões e ligações |
| `especialistas.db` | especialistas e todas as versões do conhecimento |
| `log.db` | conversas e todas as mensagens trocadas entre os agentes (JSON) |

As mensagens também vão para `logs/<conversa>.jsonl`. Pastas antigas com um único
`gigabrain.db` são convertidas sozinhas.

## API para a aplicação web

```bash
python main.py api --simulado    # sem chave, para desenvolver a tela
python main.py api               # com a DeepSeek
```

Abre em `http://127.0.0.1:8000`. A documentação interativa fica em `/docs`.

| Opção | Padrão | Uso |
|---|---|---|
| `--porta` | 8000 | |
| `--host` | 127.0.0.1 | `0.0.0.0` para acessar de outra máquina |
| `--origens` | todas | CORS, ex.: `http://localhost:5173` |
| `--token` | nenhum | exige `Authorization: Bearer <token>` |

### Rotas

Base: `/api/v1`

| Método | Rota | O que faz |
|---|---|---|
| POST | `/conversas` | abre uma conversa. Corpo opcional: `{"modo": "pos_reuniao"}` |
| POST | `/conversas/{id}/mensagens` | o PO fala. Corpo: `{"texto": "..."}` |
| POST | `/conversas/{id}/aprovar` | aprova a proposta pendente |
| POST | `/conversas/{id}/encerrar` | encerra sem aprovar |
| GET | `/conversas` | lista as conversas |
| GET | `/conversas/{id}` | mensagens da conversa (PO e Gêmeo) |
| GET | `/conversas/{id}/eventos` | tudo o que os agentes trocaram |
| GET | `/requisitos?status=&tema=&busca=` | lista os requisitos |
| GET | `/requisitos/{id}` | requisito com versões, ligações, fontes e trilha |
| GET | `/ligacoes` | todas as ligações |
| GET | `/especialistas` e `/especialistas/{id}` | especialistas e conhecimento |
| GET | `/saude` | status (não pede token) |

### Resposta de uma mensagem

```json
{
  "acao": "propor",
  "mensagem": "texto para mostrar ao PO",
  "requisitos_propostos": [{"titulo": "...", "historia": "...", "criterios_aceite": ["..."], "temas": ["..."]}],
  "salvos": [],
  "proposta_pendente": true,
  "conversa_ativa": true,
  "bastidores": [{"de": "gemeo", "para": "lider", "tipo": "consulta", "resumo": "..."}]
}
```

- `acao = perguntar`: mostre a mensagem e espere o PO.
- `acao = propor`: mostre os requisitos e o botão Aprovar.
- `acao = aprovado`: a conversa acabou. `salvos` tem os ids gravados.
- `bastidores`: o que os agentes fizeram para responder. Opcional na tela.

### Observações

- Uma mensagem pode levar até ~1 minuto. Use timeout de 120 s.
- As conversas abertas ficam na memória da API. Se ela reiniciar, essas conversas
  respondem 409 e o PO abre outra. O histórico não se perde.
- Erros: 401 token, 404 não existe, 409 conversa inativa, 422 corpo inválido, 502 falha do
  modelo. O corpo traz `{"detail": "..."}`.

### Exemplo em JavaScript

```js
const API = "http://127.0.0.1:8000/api/v1";
const post = (rota, corpo) => fetch(API + rota, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo ?? {}),
}).then((r) => r.json());

const { id } = await post("/conversas");
let r = await post(`/conversas/${id}/mensagens`, { texto: "Quero notificar o cliente quando o pagamento for confirmado" });
r = await post(`/conversas/${id}/mensagens`, { texto: "Por e-mail, em até 1 minuto" });
r = await post(`/conversas/${id}/aprovar`);          // r.salvos -> ["R1"]
const requisitos = await fetch(API + "/requisitos").then((x) => x.json());
```

## Painel

```bash
python main.py painel
```

Abre em `http://127.0.0.1:8765`. Mostra todas as pastas de dados do projeto (seletor na lateral).

| Aba | Mostra |
|---|---|
| Benchmark | resultados da avaliação, médias e testes estatísticos |
| Arquitetura | uma conversa reproduzida passo a passo |
| Conversas | as mensagens de cada conversa |
| Requisitos | lista, mapa de ligações, versões e trilha |
| Especialistas | conhecimento de cada um e o que mudou entre as versões |
| Avaliação | comparação com o gabarito de um projeto |

## Avaliação com o dataset

O dataset fica em outro repositório. Clone ao lado deste:

```bash
git clone https://github.com/schaumann-byte/EntradasGigabrain
```

```
Projetos/
├── gigabrain/
└── EntradasGigabrain/
```

São 18 reuniões simuladas, geradas por script a partir do dataset PURE, cada uma com o
gabarito dos requisitos. O projeto `2005-microcare` tem gabarito vazio e é ignorado.

### Comandos

```bash
python main.py entradas                                   # lista os projetos
python main.py avaliar 2008-keepass                       # um projeto
python main.py avaliar todos --repeticoes 5               # todos, 5 vezes cada
python main.py avaliar todos --cenario sessoes            # várias reuniões por projeto
python main.py avaliar todos --modo conselho --sem-qualidade   # sem o especialista de qualidade
```

| Opção | Padrão | Uso |
|---|---|---|
| `--modo` | todos | `conselho`, `agente_unico`, `llm_puro` (separados por vírgula) |
| `--repeticoes` | 1 | quantas vezes rodar cada projeto e modo |
| `--cenario` | reuniao | `reuniao` ou `sessoes` |
| `--paralelo` | 4 | quantas execuções ao mesmo tempo |
| `--simulado` | | sem chave |

### Modos comparados

- **conselho**: o Gêmeo lê a reunião fala por fala. Cada requisito rascunhado passa pelos
  especialistas e o Gêmeo decide se salva.
- **agente_unico**: o mesmo Gêmeo, sem os especialistas.
- **llm_puro**: a reunião inteira num pedido só, com instrução mínima.

### Cenários

- **reuniao**: uma reunião por projeto, como no dataset.
- **sessoes**: cada reunião vira 3 reuniões seguidas. Nas reuniões 2 e 3 são inseridas falas
  que **repetem** um requisito anterior (o certo é não salvar de novo) e falas que **mudam**
  um requisito anterior (o certo é salvar e ligar ao original).

### Métricas

| Métrica | O que mede |
|---|---|
| precisão | do que foi extraído, quanto existe no gabarito |
| revocação | do gabarito, quanto foi encontrado |
| F1 | média entre precisão e revocação |
| classe | acerto de FR/NFR |
| subtipo | acerto do subtipo NFR |
| rastreio | citou a fala de onde o requisito veio |
| sem respaldo | requisito que a fala citada não sustenta |
| repetições evitadas | só no cenário `sessoes` |
| mudanças ligadas | só no cenário `sessoes` |

Um requisito extraído conta como acerto quando tem pelo menos 50% de palavras em comum com
um do gabarito. Com repetições, a comparação entre modos usa o teste de Wilcoxon pareado
por projeto.

### Resultados

Ficam em `dados-avaliacao/<provedor>/<cenario>/`:

- `resumo.csv`: uma linha por projeto, modo e repetição
- `estatisticas.json`: médias e testes estatísticos
- `<projeto>/<modo>/rep-<n>/`: bancos, logs e `avaliacao.json` de cada execução

Essas pastas não vão para o Git.

### Anotação manual das ligações

O gabarito não diz se as ligações entre requisitos estão certas. Para isso:

```bash
python main.py anotacao exportar ligacoes.csv --amostra 60
```

Duas pessoas abrem o CSV e preenchem as colunas `anotador_1` e `anotador_2` com `c`
(correta) ou `i` (incorreta), sem ver a do outro. Depois:

```bash
python main.py anotacao kappa ligacoes.csv
```

Mostra a concordância entre os dois (kappa de Cohen) e a precisão das ligações.

### Parâmetros fixos

| Parâmetro | Valor | Arquivo |
|---|---|---|
| Modelo | `deepseek-chat`, JSON, até 8192 tokens de saída | `gigabrain/provedores.py` |
| Acerto com o gabarito | 50% de palavras em comum | `gigabrain/avaliacao.py` |
| Falas de contexto por rascunho | 6 | `gigabrain/agentes/gemeo.py` |
| Reorganizar conhecimento | a cada 5 requisitos | `gigabrain/conhecimento.py` |
| Cenário `sessoes` | 3 reuniões, 15% repetições, 15% mudanças, semente 42 | `gigabrain/cenarios.py` |

O rascunho do método de avaliação para o artigo está em `docs/metodo-avaliacao.md`.

## Código

| Arquivo | Conteúdo |
|---|---|
| `main.py` | linha de comando |
| `gigabrain/mensagens.py` | formato das mensagens entre agentes |
| `gigabrain/banco.py` | bancos SQLite |
| `gigabrain/conhecimento.py` | arquivo de conhecimento dos especialistas |
| `gigabrain/aprovacao.py` | quando uma fala do PO conta como aprovação |
| `gigabrain/provedores.py` | DeepSeek e o simulador |
| `gigabrain/agentes/` | Gêmeo, Líder e Especialista |
| `gigabrain/conselho.py` | conversa com o PO |
| `gigabrain/reuniao.py` | reunião processada fala por fala |
| `gigabrain/log.py` | registro das mensagens |
| `gigabrain/api.py` | API REST |
| `gigabrain/painel/` | painel |
| `gigabrain/entradas.py` | leitura do dataset |
| `gigabrain/avaliacao.py` | comparação com o gabarito |
| `gigabrain/cenarios.py` | cenário com várias reuniões |
| `gigabrain/estatistica.py` | média, desvio e Wilcoxon |
| `gigabrain/anotacao.py` | anotação manual e kappa |
| `tests/` | testes |

`arquitetura.html` e `editor.html` são a nota e o diagrama originais da arquitetura.
