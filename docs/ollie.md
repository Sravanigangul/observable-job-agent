# Ollie reads the traces

Ollie is Opik's built-in assistant. It reads your traces and answers questions
about them in the dashboard — no API, no SDK, nothing to install. Which means
this chapter is a **demo script** rather than a module: the code's only job is
to leave traces worth reading, and Phase 3 does that with one span per job
source (`src/job_scout/tools/jobs_api.py`, `traced_call` in `tracing.py`).

The point of the exercise is not that an assistant can summarize a trace.
It is that **an assistant can only find what your instrumentation records**.
We changed the instrumentation, asked the same question again, and got a
different answer. That is the whole lesson.

## What changed, and why it matters

Before Phase 3 the search produced one span for the whole cascade. A question
like "why is search slow?" could only ever be answered with "because search
takes N seconds" — true, useless. The sources are now individually spanned:

```
search
├── source.jsearch     (metadata: source, query, location)
├── source.adzuna
└── source.remotive
```

`traced_call` wraps a source **only when Opik is configured**. `opik.track`
otherwise ships spans with no API key and prints 401s into a keyless reader's
terminal, and this repo promises that everything degrades quietly without keys.

## The finding this produced on the first run

Not a worked example. This is the first real traced search after the spans
landed, on 2026-08-04:

| span | duration |
|------|----------|
| `source.jsearch` | **15 307 ms** |
| `source.adzuna` | 892 ms |
| `source.remotive` | 157 ms |
| trace total | 15 318 ms |

and `sources_used` was `['adzuna']`.

Read that again: the **primary** source spent fifteen seconds and contributed
**nothing**. 15.0s is exactly `JSearchSource`'s timeout — it is not slow, it is
timing out. Reproduced three times directly against the source: 15 264 ms,
15 265 ms, 15 244 ms, zero jobs every time.

Because the Phase 3 fan-out queries the live sources concurrently, the search's
wall time is the slowest source — so **every search pays a 15-second tax for a
result that is then discarded**. Sequential mode would be worse (15.3 + 0.9 +
0.2), not better; concurrency is not the bug, it just cannot hide this one.

Note what the old instrumentation would have told you: "search took 15
seconds." You would have gone looking at the ranking prompt.

Running the same search in both fan-out modes makes the trade-off literal
(from `notebooks/phase3_ollie.ipynb`, same day):

| mode | wall | source spans |
|------|------|--------------|
| concurrent (default) | 15 368 ms | jsearch 15 355 · adzuna 914 · remotive 218 |
| sequential | 16 228 ms | jsearch 15 248 · adzuna 979 |

Sum versus max, in two span trees — and the quota cost made concrete: the
sequential run has only *two* source spans, because Adzuna returned enough that
the cascade never needed Remotive. Concurrent had already spent that request.
Identical results, one extra API call. That is what
`SCOUT_CONCURRENT_SOURCES=false` buys back.

**Status: open, deliberately.** Lowering a source timeout changes what users
get, not just how fast they get it — a source that is merely slow today would
be dropped tomorrow. The candidate fix is a `SCOUT_SOURCE_TIMEOUT` knob applied
to all three adapters, measured the way `docs/optimizing_latency.md` measures
everything else. It is written up here rather than quietly shipped.

## The demo script

Do this live; it takes about three minutes.

**Setup.** Run a search that exercises the cascade, with tracing on:

```bash
uv run jupyter lab notebooks/phase3_ollie.ipynb    # generates both traces
```

or just use the app — any real run works.

**1. Open Ollie.** Project **job-scout** → open a search trace → the Ollie
panel (the assistant icon in the trace view). It starts with the trace you have
open as context.

**2. Ask the question you would actually ask.**

> "This search took 15 seconds. Which part was slow?"

With per-source spans it names `source.jsearch`. Without them it can only
restate the total — worth showing both if you still have an old trace around,
because the contrast IS the demo.

**3. Push on it.**

> "Did the slow source contribute any results?"

The answer is in `sources_used` on the trace output. This is where it gets
good: the slow thing was also the useless thing, and nothing about the total
duration could have told you that.

**4. Ask for the fix.**

> "The source times out at 15 seconds. What would you change?"

Judge the answer against `docs/optimizing_latency.md`, which already documents
what we tried, what worked, and one honest failure. An assistant that proposes
what we measured and rejected is a good prompt for the "trust but verify" beat.

**5. The honest close.** Ollie reads what the trace contains. Every question
above was answerable only because somebody decided a source deserved a span.
That decision is the engineering; the assistant is the interface.

## Screenshot shot-list

For the Part 3 post. Each needs a logged-in Comet with the `job-scout` project.

| # | Shot | Click path |
|---|------|------------|
| O1 | The span tree with `source.*` children, durations visible | project **job-scout** → **Traces** → a search trace → expand the span tree |
| O2 | Ollie naming the slow source | same trace → Ollie panel → ask question 1 above |
| O3 | Ollie on "did it contribute anything" | same panel, question 2 — capture the answer that cites `sources_used` |
| O4 | The `source.jsearch` span detail: 15s duration + metadata | click the `source.jsearch` span → the right-hand detail pane |
| O5 | Prompt library showing the tailor prompt's versions | project → **Prompts** → `tailor` → version history (the optimizer's change, from Phase 3) |
| O6 | The test suite result | project → **Test suites** → `job-scout-tailoring-suite` → latest run |
| O7 | Fabrication rate over time | project → **Traces** → filter `fabrication_flags > 0`, or the experiment view from `docs/phase3_findings.md` |

Opik's UI ships weekly; if a path has moved, the docs index at
`comet.com/docs/opik/llms.txt` resolves faster than clicking around.
