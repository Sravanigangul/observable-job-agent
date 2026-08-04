# Phase 3 — Ollie, and the traces worth reading

Companion notes for [`../phase3_ollie.ipynb`](../phase3_ollie.ipynb).

## What this phase adds

- **One span per job source.** `source.jsearch`, `source.adzuna`,
  `source.remotive`, each carrying the query it ran. Wrapped only when Opik is
  configured, so the keyless path stays silent.
- **Ollie as a first-class chapter.** Opik's built-in assistant reads traces in
  the dashboard. There is no SDK, so the deliverable is a demo script
  (`../../docs/ollie.md`) and traces good enough to answer real questions.
- **The voice console.** Jobvis moved into the browser over WebRTC
  (`../../docs/jobvis.md`); its client tools still resolve in Python against the
  same LangGraph checkpoint.

The measurement half of Phase 3 — validator v2, the optimizer-tuned tailor
prompt, the regression gates — is written up in
[`../../docs/phase3_findings.md`](../../docs/phase3_findings.md) with the full
B0/B1/B2 ledger. This notebook does not repeat it.

## Learning objectives

1. **An assistant can only find what you recorded.** The same question ("why is
   search slow?") against the same system gives a useless answer before the
   per-source spans and a precise one after. The engineering is the decision to
   span a source; the assistant is the interface to it.
2. **Concurrency changes the shape of a waterfall, not its worst case.** Fan-out
   turns sum-of-sources into max-of-sources. A single timing-out source is
   therefore fully exposed, not hidden — which is how we found one.
3. **A finding is not a fix.** The 15-second JSearch timeout is documented and
   left open, because lowering a source timeout changes what users get and not
   merely how fast they get it. Measure first, in the style of
   `../../docs/optimizing_latency.md`.

## Run it

```bash
uv sync --all-groups
cp .env.example .env       # OPIK_API_KEY is the one that matters here
uv run jupyter lab notebooks/phase3_ollie.ipynb
```

Then follow [`../../docs/ollie.md`](../../docs/ollie.md) in the Opik UI — the
notebook produces the traces, the doc has the click paths and the questions.

Note: the notebooks use `../data` paths, so the kernel's working directory must
be `notebooks/` (normal Jupyter behavior).

## Learning materials

- [Opik tracing concepts](https://www.comet.com/docs/opik/tracing/log_traces)
- [`../../docs/optimizing_latency.md`](../../docs/optimizing_latency.md) — the
  latency chapter this one continues, including an honest failed optimization
- [`../../docs/jobvis.md`](../../docs/jobvis.md) — the voice console
