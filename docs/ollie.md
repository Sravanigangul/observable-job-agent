# Ollie: from reading the trace to fixing the code

Ollie is Opik's assistant, and it does more than answer questions about traces.
Connected to your repository it will read the code behind a span, propose an
edit, rerun the agent on the original inputs, and run a test suite to show the
regression is gone — with your approval on every write.

That is the whole arc of this chapter, and it runs on **one real bug we left in
on purpose**.

## The bug, and why it is still here

Phase 3 gave every job source its own span (`traced_call` in `tracing.py`).
The first real traced search answered a question nobody had asked:

| span | duration |
|------|----------|
| `source.jsearch` | **15 307 ms** |
| `source.adzuna` | 892 ms |
| `source.remotive` | 157 ms |

and `sources_used` was `['adzuna']`.

The **primary** source spends fifteen seconds and contributes **nothing**.
15.0s is exactly `JSearchSource`'s timeout — it is not slow, it is timing out.
Reproduced directly, three times: 15 264 ms, 15 265 ms, 15 244 ms, zero jobs.
Because the fan-out queries live sources concurrently, wall time is the slowest
source, so **every search pays a 15-second tax for a discarded result**.

The old instrumentation would have said "search took 15 seconds" and sent you
to look at the ranking prompt.

**It is deliberately unfixed.** Not an oversight — it is the subject of the
demo below, and it is a genuine product decision (a shorter timeout drops
sources that are merely slow), which is exactly the kind of decision Ollie is
built to walk you through. **It must be fixed before `part3.0` ships**; see the
release checklist at the end.

## Connecting the repository

Capabilities 2, 3 and 4 need a local bridge. From the repo root:

```bash
uv run opik connect --project job-scout
```

Leave it running in its own terminal. To stop it:

```bash
uv run opik connect stop --project job-scout
uv run opik connect stop --all
```

**Be clear about what this grants.** While the daemon is up, Ollie can read
files in this project, propose edits to them, and run your agent. Writes
require your explicit approval each time, and the session is scoped to the
project you named — but it is still a real grant of access to a directory on
your machine, and it should be a deliberate choice rather than a step you
click past. It is also why nothing in this repo starts it for you.

## The five capabilities

Run through them in order; each one sets up the next.

### 1. Trace investigation

Open the **job-scout** project → **Traces** → a search trace → the Ollie panel.

> "This search took 15 seconds. Which part was slow?"

It should name `source.jsearch`. Without per-source spans the only honest
answer is the total you already knew — worth showing an old trace beside a new
one, because that contrast *is* the lesson: an assistant can only find what
your instrumentation recorded.

> "Did the slow source contribute any results?"

The answer is in `sources_used` on the trace output. This is the good one: the
slow thing was also the useless thing, and no amount of staring at a total
would have told you.

### 2. Source-code integration

With `opik connect` running:

> "Read the code behind the source.jsearch span and tell me where that 15
> seconds comes from."

It should find `JSearchSource.__init__` in `src/job_scout/tools/jobs_api.py`
and the `timeout: float = 15.0` default.

**Verify rather than trust.** Open the file yourself. An assistant that names
the right file and the wrong line is more dangerous than one that says it does
not know, and this is the moment to find that out — while it is cheap.

### 3. Proposing and applying a fix

> "Propose a change that stops one slow source holding up the whole search.
> Keep the cascade's consumption order and thresholds unchanged."

The shape we expect is a `SCOUT_SOURCE_TIMEOUT` setting threaded through the
three adapters, defaulting well under 15s. Judge the answer against
[`optimizing_latency.md`](optimizing_latency.md), which records what was
already tried, what worked, and one honest failure — an assistant that
proposes something already measured and rejected is worth catching.

Approve the write only when you have read the diff. Ollie edits your working
tree; `git diff` is the review.

### 4. Rerunning and verifying

> "Rerun that search with the original inputs and show me the new span tree."

Then the gate:

> "Run the job-scout-search-suite against the updated agent."

The suite (`scripts/setup_search_suite.py`) exists for exactly this. Its
assertions read the numbers the fix is supposed to move:

- no source's `duration_ms` above 8 000
- every source in `sources_used` contributed at least one job
- the search returned at least one job

**The before number, measured 2026-08-05:**

| assertion | pass |
|-----------|------|
| no source over 8s | 33% (1/3) |
| every used source contributed | 67% (2/3) |
| search returned jobs | 100% (3/3) |
| **suite pass rate** | **33%** |

A fix that works moves that to 100%. A fix that only *looks* right will not,
and that is the entire point of having the gate before the fix.

Ollie also writes a regression test with each fix. Keep an eye on where it puts
it: `gates/` in this repo is deterministic and offline by contract, so a test
that hits live job APIs belongs in the suite, not there.

### 5. Cross-workspace search

Ollie queries traces, datasets, experiments and prompts in one conversation.
Phase 2 and 3 left plenty to ask about:

> "Compare the tailoring-gpt-4.1-mini experiments before and after the prompt
> optimization."

(0.309 → 0.1423 fabrication rate; see [`phase3_findings.md`](phase3_findings.md).)

> "Show me the versions of the tailor prompt and what changed."

(The optimizer's winning instruction block is the current version.)

> "Which traces in this project have fabrication_flags above zero?"

> "What is in the job-scout-tailoring-cases dataset?"

Each answer is checkable against a number already written down in this repo,
which is the right way to build trust in a tool that reads your data: ask it
things you already know before you ask it things you do not.

## The honest close

Ollie did not find the 15-second timeout. The instrumentation did — because
somebody decided a job source deserved its own span — and Ollie read it out
loud, then followed it into the code. That is a genuinely useful thing for a
tool to do, and it is not the same as the tool doing your observability for
you. Every question above was answerable only because the trace already
contained the answer.

## Screenshot shot-list

For the Part 3 post. Each needs a logged-in Comet on the `job-scout` project.

| # | Shot | Click path |
|---|------|------------|
| O1 | Span tree with `source.*` children, durations visible | **Traces** → a search trace → expand the span tree |
| O2 | Ollie naming the slow source | same trace → Ollie panel → question 1 |
| O3 | Ollie on "did it contribute anything" | same panel, question 2 — capture the answer citing `sources_used` |
| O4 | The `source.jsearch` span detail: 15s + metadata | click the span → right-hand detail pane |
| O5 | Ollie reading `jobs_api.py` | with `opik connect` running, capability 2 |
| O6 | The proposed diff, awaiting approval | capability 3 — the approval prompt is the shot |
| O7 | Search suite red, then green | **Test suites** → `job-scout-search-suite` → the two runs side by side |
| O8 | Prompt library version history | **Prompts** → `tailor` → versions |
| O9 | Cross-workspace answer | Ollie panel, capability 5 |

Opik's UI ships weekly; if a path has moved, `comet.com/docs/opik/llms.txt`
resolves faster than clicking around.

## Release checklist

- [ ] The JSearch timeout is fixed (via Ollie on camera, or by hand if the
      demo does not land) — **do not ship `part3.0` with a known 15s tax**
- [ ] `job-scout-search-suite` green after the fix, with both runs kept for the
      before/after screenshot
- [ ] `docs/phase3_findings.md` updated with the measured after-number
