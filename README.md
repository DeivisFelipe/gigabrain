# GigaBrain

A multi-agent system for defining software requirements. The Product Owner (PO) talks to
one agent, the Digital Twin, which consults topic experts before proposing requirements.
What the PO approves goes into a local database, with versions and links between
requirements.

GigaBrain does not read source code. It only works with requirements, project documents and
what the PO says.

## Install

```bash
git clone https://github.com/DeivisFelipe/gigabrain
cd gigabrain
pip install -r requirements.txt
```

To use the real model, set the DeepSeek key:

```bash
$env:DEEPSEEK_API_KEY="your-key"      # PowerShell
export DEEPSEEK_API_KEY="your-key"    # Linux/Mac
```

Without a key, almost every command accepts `--simulated`, which answers with fixed rules.
It is for testing the flow, not for measuring quality.

## Commands

| Command | What it does |
|---|---|
| `python main.py chat` | talk to the Twin in the terminal |
| `python main.py api` | start the REST API for the web application |
| `python main.py dashboard` | open the visual dashboard in the browser |
| `python main.py demo` | run 5 sample conversations (simulated) into `data-demo/` |
| `python main.py requirements` | list the saved requirements |
| `python main.py trace R3` | show how R3 came to be |
| `python main.py experts` | list the experts |
| `python main.py feed payments minutes.md` | give a document to an expert |
| `python main.py evaluate ...` | evaluation with the dataset (see below) |
| `python main.py annotation ...` | manual annotation of the links (see below) |
| `python -m unittest discover tests` | tests (offline) |

`--data <folder>` (before the command) chooses where the databases go. Default: `data/`.

## How it works

```
PO ──▶ Digital Twin ──▶ Leader ──▶ Experts (one per topic: payments, search...)
        ▲                  │
        └─ recommendations ┘
        │
        └─▶ PO approves ──▶ Requirements Repository
```

- **Digital Twin** (Deliberative Council): talks to the PO, asks questions, proposes requirements.
- **Leader** (Consultative Council): sends each question to the right expert. Creates a new
  expert when a new topic shows up.
- **Experts**: each one owns a topic and keeps what it knows in a Markdown file. They only
  recommend.
- **Who decides:** the PO, in the conversation. In meetings processed in batch (no PO), the
  Twin decides.
- **Quality expert:** always present; checks whether the requirement is functional (FR) or
  non-functional (NFR), its subtype (performance, security...) and its format.

Nothing is saved without an explicit approval. "Approved, but change the deadline" does not approve.

## Requirement format

There is no single standard for writing requirements, so GigaBrain combines the most used ones.
Each one answers a different question:

| Field | Format | Answers | Source |
|---|---|---|---|
| `statement` | "shall" sentence in an EARS pattern | what the system must do, without ambiguity | ISO/IEC/IEEE 29148, EARS |
| `story` | As a *role*, I want *goal*, so that *benefit* | who wants it and why | agile user stories |
| `acceptance_criteria` | Given / When / Then | how to test it | BDD (Gherkin) |
| `class`, `subtype` | FR or NFR; NFR subtype from ISO/IEC 25010 | what kind of requirement it is | PROMISE/PURE |

### EARS patterns

EARS (Easy Approach to Requirements Syntax, Mavin et al., 2009) uses five sentence shapes:

| Pattern | Shape | Example |
|---|---|---|
| ubiquitous | The *system* shall *response*. | The system shall export reports to PDF. |
| event | When *trigger*, the *system* shall *response*. | When the payment is confirmed, the system shall email the customer within 1 minute. |
| state | While *state*, the *system* shall *response*. | While the user is offline, the app shall queue the orders. |
| unwanted | If *problem*, then the *system* shall *response*. | If the card is declined, then the system shall show the reason. |
| optional | Where *feature*, the *system* shall *response*. | Where a printer is available, the system shall print the receipt. |

A mix of them (e.g. "While ..., when ...") is `complex`.

### Example of a saved requirement

```json
{
  "title": "Payment confirmation email",
  "statement": "When the payment is confirmed, the system shall email the customer within 1 minute.",
  "ears_pattern": "event",
  "story": {"role": "customer", "goal": "to be told my payment went through", "benefit": "I know the order is done"},
  "acceptance_criteria": [
    {"scenario": "Confirmed payment", "given": "an order waiting for payment",
     "when": "the payment is confirmed", "then": "the customer receives an email within 1 minute"}
  ],
  "class": "NFR",
  "subtype": "PE",
  "kind": "business",
  "topics": ["payments", "notifications"]
}
```

### Automatic checks

Every requirement gets checks without an LLM (`gigabrain/requirement_format.py`). The API and the
dashboard show them next to the requirement:

| Check | OK when |
|---|---|
| `ears` | the statement has "shall"/"must" and fits a pattern |
| `story_complete` | role, goal and benefit are filled in |
| `criteria_gherkin` | every criterion has given, when and then |
| `vague_terms` | empty: no "fast", "easy", "user-friendly"... without a number |

NFR subtypes: `PE` performance, `SE` security, `US` usability, `LF` look and feel, `A`
availability, `SA` safety, `PO` portability, `MN` maintainability, `SC` scalability, `L`
legal, `FT` fault tolerance, `OT` other.

## Requirements

Each requirement has an id (R1, R2...), versions (none is ever deleted) and links to others:

| Link | Meaning | Effect on the other requirement |
|---|---|---|
| `refines` | adds detail | none |
| `splits` | is one part of it | becomes `split` |
| `merges` | was merged with others | becomes `merged` |
| `supersedes` | takes its place | becomes `superseded` |
| `depends_on` | needs it | if it changes, this one becomes `under_review` |
| `conflicts_with` | contradicts it | the PO decides |

Read it as "source *link* target": "R3 supersedes R1".

## Experts

The knowledge of each expert is in `data/knowledge/<topic>.md`, with fixed sections: business
rules, glossary, decisions, approved requirements and open points. Every change becomes a new
version in the database, with its reason. The file changes when:

- a requirement of the topic is approved;
- someone gives it a document (`python main.py feed <expert> <file>`);
- someone edits the file by hand.

## Databases

Three SQLite files in the data folder:

| File | Content |
|---|---|
| `requirements.db` | requirements, versions and links |
| `experts.db` | experts and every version of their knowledge |
| `log.db` | conversations and every message exchanged by the agents (JSON) |

The messages also go to `logs/<conversation>.jsonl`.

## API for the web application

```bash
python main.py api --simulated    # no key, to build the screens
python main.py api                # with DeepSeek
```

It runs on `http://127.0.0.1:8000`. Interactive docs at `/docs`.

| Option | Default | Use |
|---|---|---|
| `--port` | 8000 | |
| `--host` | 127.0.0.1 | `0.0.0.0` to accept other machines |
| `--origins` | all | CORS, e.g. `http://localhost:5173` |
| `--token` | none | requires `Authorization: Bearer <token>` |

### Routes

Base: `/api/v1`

| Method | Route | What it does |
|---|---|---|
| POST | `/conversations` | open a conversation. Optional body: `{"mode": "after_meeting"}` |
| POST | `/conversations/{id}/messages` | the PO talks. Body: `{"text": "..."}` |
| POST | `/conversations/{id}/approve` | approve the pending proposal |
| POST | `/conversations/{id}/close` | close without approving |
| GET | `/conversations` | list the conversations |
| GET | `/conversations/{id}` | messages of the conversation (PO and Twin) |
| GET | `/conversations/{id}/events` | everything the agents exchanged |
| GET | `/requirements?status=&topic=&search=` | list the requirements |
| GET | `/requirements/{id}` | requirement with versions, links, sources, checks and trace |
| GET | `/links` | all links |
| GET | `/experts` and `/experts/{id}` | experts and their knowledge |
| GET | `/health` | status (no token needed) |

### Answer to a message

```json
{
  "action": "propose",
  "message": "text to show the PO",
  "proposed_requirements": [{"title": "...", "statement": "...", "story": {}, "acceptance_criteria": [], "quality": {}}],
  "saved": [],
  "pending_proposal": true,
  "conversation_active": true,
  "behind_the_scenes": [{"sender": "twin", "recipient": "leader", "kind": "query", "summary": "..."}]
}
```

- `action = ask`: show the message and wait for the PO.
- `action = propose`: show the requirements and the Approve button.
- `action = approved`: the conversation is over. `saved` has the saved ids.
- `behind_the_scenes`: what the agents did to answer. Optional on screen.

### Notes

- A message may take up to ~1 minute. Use a 120 s timeout.
- Open conversations live in the API memory. If it restarts, they answer 409 and the PO
  opens a new one. The history is not lost.
- Errors: 401 token, 404 not found, 409 inactive conversation, 422 invalid body, 502 model
  failure. The body has `{"detail": "..."}`.

### JavaScript example

```js
const API = "http://127.0.0.1:8000/api/v1";
const post = (route, body) => fetch(API + route, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}),
}).then((r) => r.json());

const { id } = await post("/conversations");
let r = await post(`/conversations/${id}/messages`, { text: "I want to notify the customer when the payment is confirmed" });
r = await post(`/conversations/${id}/messages`, { text: "By email, within 1 minute" });
r = await post(`/conversations/${id}/approve`);          // r.saved -> ["R1"]
const requirements = await fetch(API + "/requirements").then((x) => x.json());
```

## Dashboard

```bash
python main.py dashboard
```

It runs on `http://127.0.0.1:8765` and shows every data folder of the project (selector on the side).

| Tab | Shows |
|---|---|
| Benchmark | evaluation results, means and statistical tests |
| Architecture | a conversation replayed step by step |
| Conversations | the messages of each conversation |
| Requirements | list, link map, versions, trace and format checks |
| Experts | knowledge of each one and what changed between versions |
| Evaluation | comparison with the gold standard of a project |

## Evaluation with the dataset

The dataset lives in another repository. Clone it next to this one:

```bash
git clone https://github.com/schaumann-byte/EntradasGigabrain
```

```
Projects/
├── gigabrain/
└── EntradasGigabrain/
```

It has 18 simulated meetings, generated by a script from the PURE dataset, each with the gold
standard requirements. Project `2005-microcare` has an empty gold standard and is skipped.

### Commands

```bash
python main.py dataset                                      # list the projects
python main.py evaluate 2008-keepass                        # one project
python main.py evaluate all --repetitions 5                 # all of them, 5 times each
python main.py evaluate all --scenario sessions             # several meetings per project
python main.py evaluate all --mode council --no-quality     # without the quality expert
```

| Option | Default | Use |
|---|---|---|
| `--mode` | all | `council`, `single_agent`, `plain_llm` (comma-separated) |
| `--repetitions` | 1 | how many times to run each project and mode |
| `--scenario` | meeting | `meeting` or `sessions` |
| `--parallel` | 4 | how many runs at the same time |
| `--simulated` | | no key |

### Compared modes

- **council**: the Twin reads the meeting one utterance at a time. Each draft goes to the
  experts and the Twin decides whether to save it.
- **single_agent**: the same Twin, without the experts.
- **plain_llm**: the whole meeting in one request, with a minimal instruction.

### Scenarios

- **meeting**: one meeting per project, as in the dataset.
- **sessions**: each meeting becomes 3 meetings in a row. Meetings 2 and 3 get extra
  utterances that **repeat** an earlier requirement (it should not be saved again) and
  utterances that **change** an earlier requirement (it should be saved and linked to the original).

### Metrics

| Metric | Measures |
|---|---|
| precision | of what was extracted, how much is in the gold standard |
| recall | of the gold standard, how much was found |
| F1 | mean of precision and recall |
| class | FR/NFR correct |
| subtype | NFR subtype correct |
| trace | cited the utterance the requirement came from |
| unsupported | requirement not backed by the cited utterance |
| ears, story, gherkin, not_vague | share of requirements that pass each format check |
| repetitions_avoided | `sessions` scenario only |
| changes_linked | `sessions` scenario only |

An extracted requirement is a hit when it shares at least 50% of its content words with one
from the gold standard. With repetitions, modes are compared with the Wilcoxon signed-rank
test paired by project.

### Results

They go to `data-eval/<provider>/<scenario>/`:

- `summary.csv`: one line per project, mode and repetition
- `statistics.json`: means and statistical tests
- `<project>/<mode>/rep-<n>/`: databases, logs and `evaluation.json` of each run

These folders are not committed.

### Manual annotation of the links

The gold standard does not say whether the links between requirements are right. For that:

```bash
python main.py annotation export links.csv --sample 60
```

Two people open the CSV and fill the columns `annotator_1` and `annotator_2` with `c`
(correct) or `i` (incorrect), without looking at each other's. Then:

```bash
python main.py annotation kappa links.csv
```

It shows the agreement between the two (Cohen's kappa) and the precision of the links.

### Fixed parameters

| Parameter | Value | File |
|---|---|---|
| Model | `deepseek-chat`, JSON, up to 8192 output tokens | `gigabrain/providers.py` |
| Hit with the gold standard | 50% of content words in common | `gigabrain/evaluation.py` |
| Context utterances per draft | 6 | `gigabrain/agents/twin.py` |
| Reorganize knowledge | every 5 requirements | `gigabrain/knowledge.py` |
| `sessions` scenario | 3 meetings, 15% repetitions, 15% changes, seed 42 | `gigabrain/scenarios.py` |

The draft of the evaluation method for the paper is in `docs/evaluation-method.md`.

## Code

| File | Content |
|---|---|
| `main.py` | command line |
| `gigabrain/messages.py` | format of the messages between agents |
| `gigabrain/database.py` | SQLite databases |
| `gigabrain/requirement_format.py` | requirement structure and format checks |
| `gigabrain/knowledge.py` | knowledge file of the experts |
| `gigabrain/approval.py` | when a PO message counts as an approval |
| `gigabrain/providers.py` | DeepSeek and the simulator |
| `gigabrain/agents/` | Twin, Leader and Expert |
| `gigabrain/council.py` | conversation with the PO |
| `gigabrain/meeting.py` | meeting processed one utterance at a time |
| `gigabrain/log.py` | records the messages |
| `gigabrain/api.py` | REST API |
| `gigabrain/dashboard/` | dashboard |
| `gigabrain/dataset.py` | reads the dataset |
| `gigabrain/evaluation.py` | comparison with the gold standard |
| `gigabrain/scenarios.py` | scenario with several meetings |
| `gigabrain/stats.py` | mean, deviation and Wilcoxon |
| `gigabrain/annotation.py` | manual annotation and kappa |
| `tests/` | tests |

`docs/architecture.html` and `docs/diagram-editor.html` are the original architecture note and diagram.
