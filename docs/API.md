# API do GigaBrain para a aplicação web

API REST (JSON) para o PO real conversar com o Gêmeo Digital e consultar o histórico de
requisitos. A documentação interativa (com "Try it out") fica em **`/docs`** e a
especificação OpenAPI em **`/openapi.json`**, que dá para importar no Postman/Insomnia
ou usar para gerar um cliente tipado.

## Subir a API

```bash
pip install -r requirements.txt

python main.py api --simulado                 # sem chave: respostas por regras, ideal para desenvolver o front
$env:DEEPSEEK_API_KEY="sua-chave"; python main.py api   # com o modelo de verdade
```

| Opção | Padrão | Para quê |
|---|---|---|
| `--porta` | 8000 | porta HTTP |
| `--host` | 127.0.0.1 | use `0.0.0.0` para acessar de outra máquina da rede |
| `--origens` | todas | CORS: ex. `--origens http://localhost:5173,https://meusite.com` |
| `--token` | nenhum | exige `Authorization: Bearer <token>` (ou `GIGABRAIN_API_TOKEN`) |
| `--dados` (antes de `api`) | `dados/` | pasta dos bancos: `python main.py --dados outra-pasta api` |

Base de todas as rotas: `http://127.0.0.1:8000/api/v1`.

## Fluxo da conversa

```
POST /conversas                      -> { "id": "c-20261013-154749", ... }
POST /conversas/{id}/mensagens       -> acao = "perguntar"   (mostra a pergunta, espera o PO)
POST /conversas/{id}/mensagens       -> acao = "propor"      (mostra os requisitos + botão Aprovar)
POST /conversas/{id}/aprovar         -> acao = "aprovado"    (conversa termina; requisitos salvos)
```

- O PO também pode aprovar escrevendo "aprovado", "pode fechar" etc. Frases com ressalva
  ("aprovado, mas troca o prazo") **não** aprovam: viram pedido de ajuste.
- Enquanto `proposta_pendente` for `true`, mostre o botão **Aprovar**. Se o PO continuar
  conversando, o Gêmeo ajusta a proposta.
- `POST /conversas/{id}/encerrar` fecha sem aprovar.
- **Tempo de resposta:** uma mensagem pode levar de alguns segundos a ~1 minuto, porque o
  Gêmeo consulta o Líder e os especialistas. Mostre um "pensando…" e use timeout de pelo
  menos 120 s. Uma conversa aceita uma mensagem por vez.

### Resposta de `POST /conversas/{id}/mensagens` e `/aprovar`

```json
{
  "acao": "propor",
  "mensagem": "Com base na conversa e nos especialistas, minha proposta é esta...",
  "requisitos_propostos": [
    {
      "titulo": "Exportar o relatório de pagamentos em PDF",
      "historia": "Como financeiro, quero exportar..., para que...",
      "criterios_aceite": ["Quando ..., o sistema ..."],
      "tipo": "negocio",
      "temas": ["pagamentos", "relatorios"],
      "base": null,
      "ligacoes": [{"tipo": "depende_de", "alvo": "R2", "motivo": "..."}]
    }
  ],
  "salvos": [],
  "proposta_pendente": true,
  "conversa_ativa": true,
  "bastidores": [
    {"id": 3, "momento": "2026-10-13T15:47:54", "de": "gemeo", "para": "lider", "tipo": "consulta", "resumo": "..."},
    {"id": 6, "momento": "2026-10-13T15:47:54", "de": "lider", "para": "registro", "tipo": "especialista_criado", "resumo": "novo especialista 'pagamentos'"}
  ]
}
```

- `acao`: `perguntar`, `propor` ou `aprovado`.
- `salvos`: com `acao = aprovado`, os ids gravados (ex. `["R1"]`).
- `bastidores`: o que os agentes fizeram para chegar à resposta (consultas ao Líder,
  especialistas criados, chamadas ao LLM...). Opcional na interface; bom para um painel
  "como o GigaBrain pensou". Filtre `tipo = chamada_llm` se quiser menos ruído.
- `base`: quando a proposta é uma **nova versão** de um requisito existente (ex. `"R3"`).
- `ligacoes`: relações com requisitos existentes (`refina`, `divide`, `junta`, `substitui`,
  `depende_de`, `conflita_com`), lidas como "o novo requisito *tipo* alvo".

## Histórico

| Rota | O que devolve |
|---|---|
| `GET /conversas` | conversas do PO (`?somente_do_po=false` inclui reuniões processadas em lote) |
| `GET /conversas/{id}` | a conversa como o PO vê: `mensagens` (autor `po`/`gemeo`, tipo, texto, requisitos propostos), `requisitos_salvos`, `ativa`, `proposta_pendente` |
| `GET /conversas/{id}/eventos?desde=0&completo=false` | todos os bastidores em ordem; `completo=true` traz o JSON inteiro |
| `GET /requisitos?status=&tema=&busca=` | lista: id, título, texto, status, versão atual, temas, datas |
| `GET /requisitos/{id}` | conteúdo atual, `versoes` (todas), `ligacoes`, `fontes` (de onde veio cada informação) e `trilha` ("como chegamos aqui") |
| `GET /ligacoes` | todas as ligações entre requisitos |
| `GET /especialistas` / `GET /especialistas/{id}` | especialistas e o arquivo de conhecimento com todas as versões |
| `GET /saude` | status e provedor (não exige token) |

Status de requisito: `ativo`, `em_revisao` (algo de que ele depende mudou), `substituido`,
`dividido`, `unido`.

## Erros

| Código | Quando |
|---|---|
| 401 | token ausente ou errado (só com `--token`) |
| 404 | conversa, requisito ou especialista não existe |
| 409 | conversa não está ativa (aprovada, encerrada ou a API foi reiniciada), ou aprovar sem proposta |
| 422 | corpo inválido (ex. `texto` vazio) |
| 502 | o modelo de linguagem falhou; a conversa continua e o PO pode reenviar |

Corpo do erro: `{"detail": "mensagem em português"}`.

**Reinício da API:** as conversas abertas ficam na memória do servidor. Se a API reiniciar,
elas passam a responder 409; o histórico continua disponível e o PO abre uma conversa nova.

## Exemplo em JavaScript

```js
const API = "http://127.0.0.1:8000/api/v1";

async function chamar(caminho, corpo) {
  const r = await fetch(API + caminho, {
    method: corpo === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" /*, Authorization: "Bearer <token>" */ },
    body: corpo === undefined ? undefined : JSON.stringify(corpo),
  });
  if (!r.ok) throw new Error((await r.json()).detail);
  return r.json();
}

const { id } = await chamar("/conversas", { modo: "pos_reuniao" });
let resposta = await chamar(`/conversas/${id}/mensagens`, { texto: "Quero notificar o cliente quando o pagamento for confirmado" });
// resposta.acao === "perguntar" -> mostrar resposta.mensagem e esperar o PO
resposta = await chamar(`/conversas/${id}/mensagens`, { texto: "Por e-mail, em até 1 minuto" });
// resposta.acao === "propor" -> mostrar resposta.requisitos_propostos e o botão Aprovar
resposta = await chamar(`/conversas/${id}/aprovar`, {});
// resposta.acao === "aprovado", resposta.salvos === ["R1"]

const requisitos = await chamar("/requisitos");
const detalhe = await chamar("/requisitos/R1");   // versões, ligações, trilha
```

## Observações

- A API e o painel (`python main.py painel`) leem os mesmos bancos: o que o PO aprova pela
  web aparece no painel na hora (selecione a pasta `dados`).
- Os textos dos requisitos ficam no idioma da conversa.
- Modo `reuniao` em `POST /conversas`: mais rápido, não cria especialistas novos (registra
  uma pendência para depois da reunião).
