# Phase 3 findings — self-improvement, measured

Phase 2 ended with documented weaknesses and the promise: fix them in Phase 3
and prove it. This report is the proof ledger. Every number here comes from
the same 15-case tailoring batch harness (`scripts/run_tailor_batch.py`) and
the same eval suites (`scripts/run_evals.py`) Phase 2 used.

## Experiment design

Three measured stages, so every gain is attributable:

| Stage | Code | What it isolates |
|-------|------|------------------|
| B0 | Phase 2 code, unchanged | fresh baseline at today's knobs |
| B1 | + segmenter v2 + validator v2 | the false-positive fixes (weaknesses #8, #9, #12) |
| B2 | + optimizer-tuned tailor prompt | the true-drift fix (weakness #7) |

Knobs held constant across all three (recorded from `.env` at run time):
`SCOUT_TAILOR_MODEL=openai:gpt-4.1-mini` (matches the Phase 2 committed
baseline model), `SCOUT_MODEL=openai:gpt-4o-mini`,
`SCOUT_FETCH_MODEL=openai:gpt-4.1-nano`, `SCOUT_MAX_REFORMULATIONS=0`,
thresholds at the shipped defaults 0.65 / 0.85 / 0.55 (the Phase 2 committed
batch was measured at 0.75 / 0.9 / 0.55 — see `docs/tailor_batch.json` —
which is why B0 is re-measured here rather than compared against it directly).

Raw run snapshots: `docs/phase3/b0_tailor_batch.json`, `b1_...`, `b2_...`.

## The before/after table

| Metric | B0 (baseline) | B1 (segmenter+validator v2) | B2 (+optimized prompt) |
|--------|---------------|------------------------------|------------------------|
| Fabrication rate | 0.2768 (62/224) | 0.2555 (58/227) | **0.1288 (30/233)** |
| Runs flagged | 14 of 14 | 14 of 14 | **13 of 14** (first-ever clean run) |
| Flags: cv_bullet / letter / skill | 29 / 30 / 3 | 28 / 27 / 3 | **1** / 29 / **0** |
| Flags in 0.55-0.65 near-miss band | 17 | 18 | 1 |
| Mean cover letter words | 195.4 | 197.2 | 203.6 |
| Batch cost (USD) | 0.049 | 0.049 | 0.049 |

The live batches are noisy (fresh searches, fresh generations), so the
validator's isolated effect was also measured the deterministic way: the 15
UNCHANGED packs stored in `job-scout-tailoring-cases` (Phase 2's traces),
re-validated under both stacks at identical thresholds:

| Stack | Same 15 stored packs |
|-------|----------------------|
| v1 segmenter + v1 validator | 0.2664 (65/244) |
| v2 segmenter + v2 validator | 0.2500 (61/244) |

Honest reading: better matching reclaims only ~6% of flags (the unit-spelling
near-misses it was built for). The bulk of the flag load is real rewrite
drift — which is the prompt's fault, not the validator's. That is what the
optimizer is for. (Reproduce: `git show <validator-v2-commit>~2:` both files,
revalidate the dataset's stored packs with each stack.)

## The optimizer run

`scripts/optimize_tailor_prompt.py --yes`: HierarchicalReflectiveOptimizer,
task + reasoning model gpt-4.1-mini, 5 trials x 12 samples, 92 LLM calls.
Metric: 1 - fabrication rate from the live `validate_pack` (deterministic; a
parse failure scores zero with the reason attached). Grounded score
0.772 -> 0.868 on the derived dataset; full history in
`docs/phase3/optimizer_result.json`; the winning instruction block now IS
`prompts/tailor.py` (Opik versions the change via `register_prompts()`).

Offline confirmation, same suite + model + dataset as Phase 2's committed
number: `fabrication_rate` (experiment `tailoring-gpt-4.1-mini`)
**0.309 -> 0.1423 (-54%)**. On gpt-4o-mini: 0.1749. Live batch agreement: B2
0.1288 vs B0 0.2768 (-53%).

## Regression gates

- `make gates`: deterministic — re-validates the stored tailoring packs and
  fails above rate 0.27 (the paired measurement 0.2500 + margin). Zero LLM
  calls, ~3s.
- Opik Test Suite `job-scout-tailoring-suite` (8 items, 3 judged assertions,
  `scripts/setup_test_suite.py`): the dashboard-visible gate; exits nonzero
  under 75% pass rate. Judged, therefore reported, never blindly trusted.

## Latency: the fan-out

`SCOUT_CONCURRENT_SOURCES` (default on) collapses the source cascade's
stacked waits to the slowest single source: 3.01s -> 2.01s in the
deterministic thin-primary bench, no-op within noise when the primary is
rich. Details + trade-off in `docs/optimizing_latency.md` (Phase 3
addendum).

## Open, and deliberately so: the 15-second JSearch timeout

Per-source spans (`traced_call`) exposed one on their first real run:
`source.jsearch` spends **15.3s** — exactly its timeout — and returns **zero
jobs**, on every search. Reproduced three times directly (15 264 / 15 265 /
15 244 ms). With the concurrent fan-out, wall time is the slowest source, so
every search pays that tax for a result the cascade then discards
(`sources_used: ['adzuna']`).

It is **not fixed here on purpose**: it is the subject of the Ollie codebase
loop in [`ollie.md`](ollie.md) — diagnose from the trace, read `jobs_api.py`,
propose `SCOUT_SOURCE_TIMEOUT`, rerun, verify against
`job-scout-search-suite` (currently **33%**, red in exactly the right places).
A shorter timeout also drops sources that are merely slow, which is a product
decision rather than a bug fix.

**This must be resolved before `part3.0` ships.** Shipping a release with a
known 15-second tax on every search would be indefensible, however good the
demo is.

## Weakness-by-weakness status

Filled in as the work lands; every Phase 2 weakness gets a verdict here.

| # | Weakness (Phase 2) | Status |
|---|--------------------|--------|
| 7 | Tailor prompt drifts, every run flagged | **FIXED by the optimizer**: HRPO against the deterministic grounding metric, 0.772 -> 0.868 on the optimization set (92 LLM calls, `docs/phase3/optimizer_result.json`); live batch bullet flags 28 -> 1, rate 0.2555 -> 0.1288, one run fully clean. Residual: cover-letter claims (29 flags) are now ~all of the flag load |
| 8 | Thresholds flag honest rewrites (near-miss band) | **FIXED (validator v2)**: unit canonicalization + skill containment; paired revalidation of identical packs 0.2664 -> 0.2500; near-miss band 17 -> 1 by B2 |
| 9 | Compositional truths flagged / embedded lies pass | partially fixed (pair references); embedded-lie false negative remains, documented |
| 10 | Judgment depends on the judge (0.44 vs 0.84) | calibration pending hand labels |
| 11 | Online rules cannot read attachments | **expired upstream**: Opik ships attachment-reading LLM-judge rules since 2026-06-23 (agentic tool loop, up to 8 attachments). The cv_text fallback can retire; docs updated in Part 3 packaging |
| 12 | Naive segmentation pollutes the corpus | **FIXED (segmenter v2)**: wrapped-line joining, flexible skill headings, pipe rows kept as content. Still a heuristic by design |
| 1-6 | Phase 1 carryovers (Adzuna underuse, ignored locations, reformulation churn, cost/latency, score compression, matched_skills grounding) | latency addressed in `docs/optimizing_latency.md` + concurrent source fan-out (this phase); rest re-measured at wrap |
