# Evals

The vault is its own benchmark. Before trusting a new model or harness, run the golden set.

## Files
- `golden.example.yaml`: five questions against `vault.example/`. Your real set lives in `<vault>/.harness/golden.yaml` (private, 15+ questions).
- `suite.yaml`: the evalblink suite definition (step 10 of the build order).
- `adapter.py`: runs the harness headless from the vault root for each question and returns `{answer, cited_files, files_opened}`.

## Graders
| Grader | Checks | Type |
| --- | --- | --- |
| `cited_files` | every expected path appears as a citation in the answer | exact |
| `must_include` / `must_not_include` | strings | exact |
| `behaviour` | the one-sentence rubric, scored 0/1 by a judge model at temperature 0 | judge |

Calibrate the judge once: 20 answers labelled by hand, agreement ≥ 0.80, then a cheap model is enough.

## Threshold and baseline
Pass ≥ 0.80. The first passing run is frozen in `<vault>/.harness/baseline/` with model, harness, date. Later runs are compared to it.

## The four scaling gates
Printed by `anybrain eval` (PRD section 22.4). Any ✗ means add QMD (PRD section 17.2). Until then, indexes are enough.
