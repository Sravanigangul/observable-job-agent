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
| Fabrication rate | 0.2768 (62/224) | 0.2555 (58/227) | pending |
| Runs flagged | 14 of 14 | 14 of 14 | pending |
| Flags: cv_bullet / letter / skill | 29 / 30 / 3 | 28 / 27 / 3 | pending |
| Flags in 0.55-0.65 near-miss band | 17 | 18 | pending |
| Mean cover letter words | 195.4 | 197.2 | pending |
| Batch cost (USD) | 0.049 | 0.049 | pending |

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

## Weakness-by-weakness status

Filled in as the work lands; every Phase 2 weakness gets a verdict here.

| # | Weakness (Phase 2) | Status |
|---|--------------------|--------|
| 7 | Tailor prompt drifts, every run flagged | in progress (optimizer) |
| 8 | Thresholds flag honest rewrites (near-miss band) | in progress (validator v2) |
| 9 | Compositional truths flagged / embedded lies pass | partially fixed (pair references); embedded-lie false negative remains, documented |
| 10 | Judgment depends on the judge (0.44 vs 0.84) | calibration pending hand labels |
| 11 | Online rules cannot read attachments | **expired upstream**: Opik ships attachment-reading LLM-judge rules since 2026-06-23 (agentic tool loop, up to 8 attachments). The cv_text fallback can retire; docs updated in Part 3 packaging |
| 12 | Naive segmentation pollutes the corpus | in progress (segmenter v2) |
| 1-6 | Phase 1 carryovers (Adzuna underuse, ignored locations, reformulation churn, cost/latency, score compression, matched_skills grounding) | latency addressed in `docs/optimizing_latency.md` + concurrent source fan-out (this phase); rest re-measured at wrap |
