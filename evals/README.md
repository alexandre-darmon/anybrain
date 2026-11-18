# Evals

The vault is its own benchmark. Before trusting a new model or harness, run the golden set.

```bash
anybrain eval <vault>                               # Claude Code, frontier profile
anybrain eval <vault> --harness opencode --profile local
anybrain eval vault.example --golden evals/golden.example.yaml --max-cost 5
anybrain eval <vault> --freeze                       # mark this run as the baseline
anybrain trace <vault> show                          # what each run read, wrote and cost
```

## Files

- `golden.example.yaml`: ten questions against `vault.example/`, one or more per tag. Your own set lives in `<vault>/.harness/golden.yaml` (private, at least 15 questions).
- `rubric.md`: what the judge grades and how it answers.

## How a run works

1. Each case runs headless (`[harnesses.<name>] headless` in `.harness/models.toml`) in a throwaway copy of the vault, so an eval never changes your vault.
2. The harness's machine output becomes a run trace (PRD 22.5): files read and written, tool calls, denied calls, tokens, cost, and any local-only file read on the frontier profile.
3. Graders (PRD 22.2): `cited_files`, `must_include`, `must_not_include` (exact); `behaviour`, graded by a judge in a fresh context that sees only the question, the expected behaviour, the answer and the cited paths. The judge is `endpoint:<profile>` (an OpenAI-compatible endpoint at temperature 0) or `harness:<name>` (a harness run in an empty folder with no tools, through its `[harnesses.<name>] judge` command), set in `[eval]`. A `--profile local` run needs a judge on the local profile.
4. A frontier run that reads a local-only file fails, whatever its answer. A failed `safety` or `injection` case makes the command exit with code 1.
5. The run file goes to `.harness/baseline/run-<harness>-<profile>-<date>.json`, readable on the local profile only. `--freeze` makes it the baseline; later runs print the change per tag.

Calibrate the judge once: 20 answers labelled by hand, agreement at least 0.80, before trusting it.

## The scaling gates (PRD 17.3)

| Gate | Threshold | Measured from |
| --- | --- | --- |
| Recall | expected file cited in at least 90% of `navigation` cases | `cited_files` |
| Multi-hop | at least 80% pass on `multi-hop` cases | tag |
| Index weight | root index plus largest domain index at most 5,000 tokens | the vault's indexes |
| Needle share | `needle` cases at most 20% of the set, passing at least 70% | tag |
| Cost | median files read per case at most 8 | run traces (measured, not self-reported) |

Any failed gate means: add QMD (PRD 17.2), then raise the `neighbors` depth.
