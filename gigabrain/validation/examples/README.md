# Example inputs

Two files, each with a job. They run the validation without any agent, and the tests use them
as a contract (`tests/validation/test_report_and_cli.py`): if their behaviour changes, a test
fails and this page stops being true.

Both are envelopes (`schema_version` 1.1) in the same domain, the moderation of a forum: the
turns of a short meeting (`T2`, `T4`, `T6`), a written need (`NEED-001`), a glossary, an
assembled document and requirements in the structure the agents emit.

## `good.json`

Four requirements that pass every check: an EARS statement, a story in three parts, Gherkin
criteria with an observable result, a class, and a cited source that supports the statement.
`R1` to `R3` cite a turn of the meeting; `R4` quotes the written need word for word.

```bash
python -m gigabrain.validation.cli gigabrain/validation/examples/good.json
```

Expected: **APPROVED**, no findings, all 45 checks run. It is the reference of "what passes".

## `bad.json`

The same domain with defects seeded on purpose. It is an acceptance test you can read.

```bash
python -m gigabrain.validation.cli gigabrain/validation/examples/bad.json --format markdown
```

Expected: **REJECTED**, 3 blocking, 21 major and 19 minor findings from 35 of the 45 checks,
touching all ten competencies.

| Artefact | What is wrong with it | Fails |
|---|---|---|
| `B1` | "Moderation needs to be fast and intuitive": no "shall", no story, no acceptance criteria, no class, no source, and two subjective words | conforming, unambiguous, correctness |
| `B2` | cites a turn that is not in the input (`T99`) and one that does not say what the statement says; claims to repeat `B8`, with which it shares almost nothing; uses a link kind that does not exist; says "always"; a criterion without a Then and another that shows nothing observable | conforming, verifiable, correctness |
| `B3` | a sound requirement with one criterion only, and "moderation queue" where the glossary says "review queue" | passes, with a remark |
| `B4` | the same requirement as `B3`, and its Then only repeats the statement | passes alone; fails together (non-redundancy) |
| `B5` | "basically", "fast", "if possible", "good experience", "many", two obligations in one sentence, criteria that rely on "fast" and "adequate", an NFR subtype that does not exist | unambiguous, verifiable |
| `B6` | the negation of `B3` and `B4` | passes alone; suspected contradiction together |
| `B7`, `B8` | the same requirement with two limits (2 and 4 seconds) | pass alone; value conflict together |
| `B9` | quotes a sentence its source does not contain | correctness (blocking) |

And, at the document level: a need that produced no requirement (`NEED-002`), a required
section that is missing (the glossary), another that is empty, an introduction made of filler,
and a source that does not say where it came from (`T4`).

`B3`, `B4`, `B6`, `B7` and `B8` are the point of the second level: each one passes on its own,
and the defect only exists when they are read together.

## Useful variations

```bash
# what an agent receives when the artefact goes back to it
python -m gigabrain.validation.cli gigabrain/validation/examples/bad.json --format feedback

# ablation: switch one check off and compare
python -m gigabrain.validation.cli gigabrain/validation/examples/bad.json --format json \
       --disable grounding.quote_supported

# a stricter and a looser gate
python -m gigabrain.validation.cli gigabrain/validation/examples/good.json --policy strict
python -m gigabrain.validation.cli gigabrain/validation/examples/bad.json --policy lenient

# keep the report (one JSON line per run)
python -m gigabrain.validation.cli gigabrain/validation/examples/good.json --save-to reports.jsonl
```

With `--policy lenient` the bad example is still rejected: the lenient gate lets poor
requirements through, not unusable ones, and three findings are blocking.
