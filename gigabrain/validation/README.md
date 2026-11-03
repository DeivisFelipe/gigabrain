# Validation layer

The code behind the **Critic** agent (`gigabrain/agents/critic.py`). It takes what the agents
produce and answers three questions, in this order:

1. is the requirement well formed and can it be verified?
2. is what it states grounded in a real source?
3. does the set hold together: no contradiction, no repetition, no gap?

What this layer does **not** do: write requirements (the Twin and the experts do), keep the
links between requirements (the repository does) or decide what is saved (the PO does, or the
Twin in a meeting processed in batch).

The first tier is deterministic and calls no LLM: same input, same rule set, same report. A second
tier, an LLM judge (`judge.py`, `--validation-judge`, off by default), asks what the first cannot see.

## Two levels, ten competencies

| Level | Competencies |
|---|---|
| item (one requirement) | conforming, unambiguous, verifiable, correctness (grounded in its source), understandable |
| document (the requirements together) | internal consistency, non-redundancy, completeness, conciseness, traceability |

Every competency gets a score from 0 to 1. A requirement **passes only if every competency
passes**: a score of at least 0.70 and no blocking finding. What does not pass goes back to the
agent that wrote it, with the list of what to fix.

## Run

```bash
# inside the system
python main.py chat --simulated                      # the Critic scores every proposal
python main.py evaluate 2008-keepass --simulated     # the evaluation gets the "valid" column
python main.py validate                              # score the repository in data/
python main.py validate R3 --format markdown         # one requirement against the others

# on a file, outside the system
python -m gigabrain.validation.cli gigabrain/validation/examples/good.json
python -m gigabrain.validation.cli gigabrain/validation/examples/bad.json --format markdown
python -m gigabrain.validation.cli --list-checks

# tests
python -m unittest discover tests
```

Options of the Critic, on `chat`, `evaluate` and `api`:

| Option | Default | Use |
|---|---|---|
| `--no-validation` | | run without the Critic (ablation) |
| `--validation-rounds N` | 1 | how many times an artefact may go back to the agent that wrote it; 0 = score only |
| `--validation-strict` | | `evaluate` only: discard what still fails after the rounds |
| `--validation-judge` | off | tier 2: an LLM judge reports content the sources do not say (one more call per requirement version) |

`demo` takes the same options, except `--validation-strict`. A broken layer does not stop a run with
`--no-validation`; asked for, it stops the command with an error that says so. The read routes of the API
and the dashboard always compute the scores, and answer `validation: null` if the layer fails.

Options of `validate` and of the standalone command line: `--format console|markdown|json|feedback`,
`--policy default|strict|lenient`, `--disable CHECK_ID` (may be repeated). Exit code: `0`
approved, `1` rejected, `2` invalid input.

## As a library

```python
from gigabrain.validation import ValidationEngine, load_envelope, to_markdown

report = ValidationEngine().validate(load_envelope("gigabrain/validation/examples/bad.json"))
print(report.verdict, [d.value for d in report.failed_dimensions()])
print(to_markdown(report))
```

Inside the system, the Critic is in the context:

```python
from gigabrain.agents import ValidationSettings
from gigabrain.council import open_context

ctx = open_context("data", provider, validation=ValidationSettings(rounds=1))
report = ctx.critic.validate_repository({"R3"})
```

## Files

| File | Content |
|---|---|
| `model.py` | vocabulary: level, competency, severity, artefact, finding, report |
| `registry.py` | catalogue of checks and `CheckConfig` (the thresholds) |
| `checks/` | the 45 checks, one module per competency |
| `lexicons.py` | word lists (English and Portuguese) |
| `text.py` | normalisation, similarity, coverage, quote check |
| `scoring.py` | score per competency |
| `policy.py` | the gate: `default`, `strict`, `lenient` |
| `engine.py` | runs the checks and builds the report |
| `report.py` | Markdown, JSON, console, compact summary, feedback for the agent |
| `judge.py` | second tier: the LLM judge |
| `ports.py` | what the layer needs from outside, with neutral implementations |
| `host.py` | adapters to GigaBrain's data: agents, repository, event log |
| `envelope.py` | JSON contract for running outside the system |
| `adapters/` | reports in a JSONL file, for the command line |
| `cli.py` | standalone command line |
| `examples/` | one input that passes and one with seeded defects |

## Adding a check

A check is a small function registered by a decorator. The engine does not know any check by
name.

```python
# gigabrain/validation/checks/ambiguity.py
@item_check(
    id="ambiguity.my_rule",
    dimension=Dimension.UNAMBIGUOUS,
    severity=Severity.MINOR,
    description="The statement does not say 'somehow'.",
)
def my_rule(artifact: Artifact, ctx: CheckContext) -> Iterable[Finding]:
    if "somehow" in artifact.statement.lower():
        yield Finding(
            check_id="ambiguity.my_rule",
            dimension=Dimension.UNAMBIGUOUS,
            severity=Severity.MINOR,
            level=Level.ITEM,
            target_id=artifact.id,
            locator="statement",
            message="The statement says 'somehow'.",
            suggestion="Say how.",
        )
```

Add a test next to the others in `tests/validation/`, and bump `RULESET_VERSION` in
`scoring.py`: scores of two rule sets are not comparable.

A check that depends on material that may be missing (sources, a glossary, the whole
specification) declares it with `applies=`. When the material is not there the check is skipped
and the report says why: **"I do not know" is never reported as "it does not exist"**.
