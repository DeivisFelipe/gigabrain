# Rascunho: método de avaliação

> Rascunho para a seção de avaliação do artigo. Os números ficam para depois das
> execuções com a DeepSeek; tudo o que está entre [colchetes] precisa ser preenchido
> ou decidido pelo grupo.

## Perguntas de pesquisa

- **QP1 (qualidade de extração).** A arquitetura multiagente mantém a qualidade de
  extração de requisitos de reuniões em relação a um agente único e a um LLM sem
  engenharia de prompt?
- **QP2 (coerência entre reuniões).** Ao longo de várias reuniões sobre o mesmo
  sistema, a arquitetura evita requisitos repetidos e relaciona mudanças aos
  requisitos que elas alteram?
- **QP3 (qualidade das ligações).** As ligações criadas entre requisitos (refina,
  depende de, conflita com, substitui) são corretas segundo avaliadores humanos?
- **QP4 (custo).** Qual o custo adicional, em chamadas ao LLM, tokens e tempo?

## Configurações comparadas

| Configuração | Descrição |
|---|---|
| **GigaBrain (conselho)** | O Gêmeo Digital lê a reunião fala por fala e rascunha requisitos; cada rascunho é revisado pelo especialista do tema e pelo especialista de qualidade (Conselho Consultivo, que só recomenda); o Gêmeo decide (Conselho Deliberativo) e grava no Repositório; o especialista atualiza seu conhecimento. |
| **Agente único** | O mesmo Gêmeo, com o mesmo prompt e a mesma leitura fala por fala, sem o Conselho Consultivo. Isola o efeito do conselho. |
| **LLM puro** | A transcrição inteira num único pedido com instrução mínima. Isola o efeito da engenharia do Gêmeo. |
| **Regras (sem LLM)** | Cada frase de stakeholder vira um requisito; classe por palavras-chave. Piso de referência. |

Todas as configurações usam o mesmo modelo ([deepseek-chat]) e recebem os requisitos já
gravados no Repositório como contexto (para não favorecer o conselho no cenário com
várias reuniões).

**Ablação:** conselho sem o especialista de qualidade (`--sem-qualidade`), para medir a
contribuição dele na classificação FR/NFR e no subtipo.

## Dados

- **Dataset de reuniões** (EntradasGigabrain): 18 transcrições simuladas derivadas do
  PURE, com gabarito de requisitos, classe FR/NFR, subtipo NFR e turnos de origem.
  Um projeto (2005-microcare) tem gabarito vazio e foi excluído; restam 17 projetos e
  [820] requisitos.
- **Cenário com várias reuniões** (construído neste trabalho): cada transcrição é dividida
  em 3 sessões, cortadas no início de um tópico do analista. Nas sessões 2 e 3 são
  injetadas falas com gabarito conhecido:
  - *repetições* (15% dos requisitos): um stakeholder repete um requisito de uma sessão
    anterior; o esperado é não gravá-lo de novo;
  - *mudanças* (15%): um stakeholder altera um requisito anterior (outro valor numérico
    ou uma restrição nova); o esperado é gravar a versão nova ligada à original.
  A geração é determinística (semente fixa) e não usa LLM.

## Métricas

| Métrica | Definição |
|---|---|
| Precisão, revocação, F1 | Pareamento um a um com o gabarito por similaridade de palavras de conteúdo (Dice ≥ 0,5) |
| Classe | acerto FR/NFR entre os pares |
| Subtipo | acerto do subtipo entre os pares NFR |
| Rastreio | o requisito cita o turno onde foi dito |
| Sem respaldo | requisito cujas falas citadas cobrem menos de 30% das suas palavras (proxy de alucinação) |
| Repetições evitadas | repetições injetadas que não geraram requisito novo |
| Mudanças ligadas | mudanças injetadas gravadas e ligadas ao requisito original |
| Precisão das ligações | proporção de ligações julgadas corretas por dois anotadores; concordância por kappa de Cohen |
| Custo | chamadas ao LLM, tokens de entrada e saída, tempo por reunião |

## Protocolo

1. Cada (projeto, configuração) é executado [5] vezes; o LLM não é determinístico.
2. Para cada projeto, calcula-se a média das repetições.
3. As configurações são comparadas par a par com o teste de Wilcoxon dos postos
   sinalizados, pareado por projeto (n = 17), com p exato; o tamanho de efeito é a
   correlação rank-biserial pareada. Nível de significância: [0,05], [com/sem] correção
   para múltiplas comparações [Holm].
4. Para a QP3, sorteiam-se [60] ligações das execuções do conselho; [dois] membros do
   grupo as julgam de forma independente.

## Ameaças à validade

- **Construção.** As transcrições são sintéticas e geradas por script, preservando as
  palavras dos requisitos; reuniões reais são mais difíceis. O pareamento por palavras
  aproxima a equivalência semântica: [validar numa amostra manual].
- **Interna.** Os prompts foram ajustados pelos autores olhando o projeto KeePass;
  [reportar resultados com e sem esse projeto]. O mesmo modelo é usado em todas as
  configurações.
- **Externa.** Um único modelo de linguagem e um único dataset; [repetir com um
  segundo modelo, se houver tempo].
- **Conclusão.** 17 projetos limitam o poder estatístico; diferenças pequenas podem não
  ser detectadas.

## Pacote de replicação

Código, dataset, logs de todas as execuções (mensagens JSON de cada agente, incluindo as
chamadas ao LLM) e o guia `REPRODUCAO.md`.
