# The Observable Job Agent
## Job Scout: a real AI agent you can see inside

<div align="center">
  <h3>Build an AI job-matching agent, and the observability to trust it</h3>
  <p>Upload your CV (PDF). Get real job openings ranked 0–100 for fit, each with an honest explanation of what matches and where your gaps are.</p>
  <p>Master the most in-demand AI engineering skills: <strong>LLM agents (LangGraph)</strong> and <strong>LLM observability (Opik / LLMOps)</strong></p>
</div>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-blue.svg" alt="Python Version">
  <img src="https://img.shields.io/badge/LangGraph-1.1+-ff6f00.svg" alt="LangGraph">
  <img src="https://img.shields.io/badge/Opik-observability-6f42c1.svg" alt="Opik">
  <img src="https://img.shields.io/badge/Gradio-5+-f97316.svg" alt="Gradio">
  <img src="https://img.shields.io/badge/tests-35%20passing-brightgreen.svg" alt="Tests">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
</p>

</br>

<p align="center">
  <img src="docs/images/architecture.png" alt="Job Scout architecture" width="820">
</p>

## 📖 About This Project

**Job Scout** is a real, observable AI agent you run on your own machine. Upload
your CV and it extracts a structured profile, searches real job openings, and
ranks each with a **fit score (0–100)** plus an honest explanation of the match
and the gaps. Every LLM and tool call is traced in
[Opik](https://www.comet.com/docs/opik/) from the very first run.

It is the first entry in *The Observable Job Agent*, a series that builds one
agent while building the ability to see inside it: **Build → Evaluate →
Self-Improve**.

> **🎯 The observability difference:** most tutorials add telemetry at the end.
> We don't. The agent gets instrumented *before* it gets good, so cost, latency,
> and quality are measurable from run one. The whole series builds on that
> decision.

> **The human applies. The agent never submits applications.** The bottleneck in
> a job hunt is the research and tailoring per application, not clicking submit.
> That is the part worth automating.

### 🧭 What You'll Build

- **End-to-end agent:** CV (PDF) → typed profile → LLM-driven job search → batched fit ranking → a bounded reformulation loop
- **LLM-driven tool use:** the model chooses the search arguments; your code executes them, and you can watch it choose in the trace
- **Full Opik tracing on every run:** a span tree per node, an auto-drawn agent graph, per-run cost, the CV attached to the trace, and versioned prompts — all from one line of code
- **A Gradio interface** with streamed progress and a run footer (cost, latency, deep link to the trace)
- **Multi-source job search** (JSearch, Adzuna, Remotive, offline cache) that runs with **zero API keys**
- **An honest baseline**: run it at scale, measure everything, document the weaknesses, and resist fixing them too early
- **Phase 2 — application preparation:** select a ranked job and get a tailored cover letter + a reworded one-page CV (LaTeX → PDF), where every bullet carries a `corpus_ref` back to your real experience and a **deterministic fabrication validator** flags anything it can't ground — visibly, never silently
- **Phase 2 — the evaluation stack:** verifiable checks (field accuracy, fabrication rate) vs unverifiable judgments (G-Eval, hallucination judges), datasets built from real traces, ≥2 comparable experiments, agent trajectory metrics, online rules, and a judge-vs-human calibration table

### 📚 The Series

| Part | Focus | Outcome | Tag |
|------|-------|---------|-----|
| **1** | **Build** | Working agent + Gradio UI + Opik tracing from run one + a documented baseline | `phase-1` |
| **2 (this repo)** | **Extend, then evaluate** | Corpus-grounded tailoring (cover letter + LaTeX CV) with a deterministic fabrication validator + the full eval stack: hand-labeled and trace-exported datasets, LLM judges, trajectory metrics, online rules, judge-vs-human calibration | `phase-2` |
| 3 | Self-improve | Test suites, prompt optimization, and trace-driven fixes with before/after numbers | `phase-3` |

Read the deep dives on [Jam with AI](https://jamwithai.substack.com).

---

## 🚀 Quick Start

### 📋 Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** package manager
- API keys are **optional** — the app runs with none.

### ⚡ Get Started

```bash
# 1. Clone
git clone https://github.com/jamwithai/observable-job-agent
cd observable-job-agent

# 2. Install
uv sync --all-groups

# 3. Configure (all keys optional)
cp .env.example .env

# 4. Verify (no network, no credits)
make test

# 5. Launch the app, then upload a CV from data/fixture_cvs/
make app
```

**Runs with zero keys** via Remotive + a committed offline cache. Add keys when
you want live international jobs, real ranking, and tracing. Every key has a free
path — see [`docs/opik_setup.md`](docs/opik_setup.md).

**Default model** is `openai:gpt-4o-mini`. Swap it with one env var: `SCOUT_MODEL`
(e.g. `groq:llama-3.3-70b-versatile` for free, or `ollama:llama3.2` for local).
Free models correctly show $0.00 cost in Opik.

### 📊 Access Points

| Service | URL | Purpose |
|---------|-----|---------|
| **Gradio UI** | http://localhost:7860 | Upload a CV, review the profile, find ranked jobs |
| **Opik dashboard** | your Comet project | Span tree, agent graph, per-run cost, prompt library |

---

## 🏗️ Architecture

CV extraction is a preprocessing step (`job_scout.profile`) that produces a typed
`Profile`; the graph takes that profile and focuses on finding jobs:

```
extract_profile(cv) → Profile ─┐
                               ▼
  START → [route_entry] ── no selected job ──→ fetch_jobs → rank_jobs → [enough good matches?]
              │                                    ↑                          │ no
              │                                    └──── reformulate_query ◄──┘   │ yes → END
              └── selected_job_id set ──→ tailor → validate_tailoring → END
                  (reads profile + ranked jobs from the thread's checkpoint — nothing re-runs)
```

- **`fetch_jobs`** is an LLM tool-calling node: the model *chooses* the `search_jobs` arguments (query, country, remote).
- **`rank_jobs`** scores postings in batches of 5, one structured-output call per batch, capped at `SCOUT_MAX_JOBS` (default 10). Already-scored jobs keep their scores across reformulation loops — only new postings hit the model.
- **`reformulate_query`** broadens the search if fewer than 5 jobs score ≥ 60, bounded to at most 2 loops. That conditional edge is what makes this an agent rather than a straight-line workflow.
- **`tailor`** (Phase 2) runs as a *second invocation on the same thread*: it reads the search results from the checkpoint and selects/rewords items from your **CandidateCorpus** (CV + optional official LinkedIn data export — never scraped). **`validate_tailoring`** then checks every claim deterministically — its thresholds are env-tunable (`SCOUT_FAB_*`), and each report records the values it ran with in the Opik trace, so tuning them against your own CV is a measurable exercise. The PDF renders via `tectonic` (`brew install tectonic`); without it you get the `.tex` + an Overleaf pointer.

The search fans out over **JSearch** (primary, city-level), **Adzuna**
(international), **Remotive** (keyless remote), and a **committed offline cache**.
Full walkthrough: [`docs/architecture.md`](docs/architecture.md). Adding a source:
[`docs/extending_sources.md`](docs/extending_sources.md).

---

## 🎙️ Jobvis — the voice concierge (optional)

Ask out loud — *"what jobs are available?"* — and a dry English butler reads you
your top three matches with fit scores and gaps, starts a search or tailors an
application on request, and the finished PDF pops up on screen by itself.
Built on **ElevenLabs Agents** with the app's Python functions registered as
client tools: the agent can only speak what the LangGraph checkpoint returns —
the Phase 2 grounding contract, applied to voice (watch the tool calls appear in
the transcript panel).

```bash
brew install portaudio   # system audio dep (pyaudio builds against it)
# put ELEVENLABS_API_KEY in .env — free tier: 15 conversation min/month.
# The key needs the Agents Platform (Conversational AI) scopes enabled.
make jobvis-agent        # create the agent, copy the printed id into .env
make app-voice           # `make app` + the voice extra — the Jobvis strip appears
```

(Plain `make app` re-syncs the venv *without* the extra — uv strips it — so use
`make app-voice` whenever you want the voice strip.)

Job Scout **remembers your resume between runs**: the extracted profile is
saved locally (`data/candidate/`, gitignored), so a restart opens straight on
step 2 and Jobvis knows you immediately — "Start over" is what forgets it.
Jobs are deliberately *not* persisted (postings go stale): each session
fetches fresh, one spoken "find me jobs" away.

Two surfaces, one session: the wizard at `/` for the manual click-flow, and
**Jarvis mode** at http://localhost:7861 — a full-dark voice console (breathing orb, live
top-matches, application panel with PDF downloads) where everything after the
CV upload happens by voice, with Jobvis leading each step.

Worth knowing: macOS asks for **microphone permission on your terminal app**
(not the browser); one voice-triggered run at a time (don't click the wizard's
own buttons mid-run); you can stop the voice session during a long run — the
result still pops. Full chapter, demo script, and troubleshooting:
[`docs/jobvis.md`](docs/jobvis.md).

---

## 🔧 Reference

### 🛠️ Technology Stack

| Component | Purpose |
|-----------|---------|
| **LangGraph** | The agent graph + conditional reformulation loop |
| **LangChain** | `init_chat_model`, LLM-driven tool calling |
| **Opik / Comet** | Observability: traces, per-run cost, versioned prompts |
| **Gradio** | Three-step wizard UI with streamed progress |
| **Pydantic + pydantic-settings** | Typed schemas and configuration |
| **httpx + pypdf** | Job-source HTTP and CV reading |
| **Job sources** | JSearch, Adzuna, Remotive, committed offline cache |

**Development tools:** uv, Ruff, Pytest, pre-commit.

### 🏗️ Project Structure

```
observable-job-agent/
├── src/job_scout/
│   ├── app.py          # Gradio four-step wizard UI (Resume → Profile → Jobs → Tailor)
│   ├── runner.py       # run orchestration shared by UI + batch (tracing, cost, latency)
│   ├── profile.py      # CV text → Profile (pre-graph extraction)
│   ├── corpus.py       # CandidateCorpus: CV + optional LinkedIn export (the grounding source)
│   ├── validation.py   # deterministic fabrication validator (difflib, zero LLM)
│   ├── renderer.py     # Jinja2 → LaTeX → PDF via tectonic (degrades to .tex)
│   ├── config.py       # Settings (pydantic-settings, SecretStr keys)
│   ├── llm.py          # chat-model factory + per-run call budget
│   ├── tracing.py      # all Opik wiring in one module
│   ├── evals/          # metrics.py: ProfileFieldAccuracy, FabricationRate, FitExplanationQuality
│   ├── voice/          # Jobvis: bridge.py, tools.py, persona.py, session.py (ElevenLabs Agents)
│   ├── graph/          # graph.py (entry router + search + tailor), state.py, schemas.py, nodes/, prompts/
│   ├── templates/      # cv.tex.j2 — the single ATS-friendly CV template
│   └── tools/          # jobs_api.py (JSearch/Adzuna/Remotive/cache), cv_reader.py, research.py (Tavily)
├── scripts/            # run_batch.py, run_tailor_batch.py, build_*_dataset.py, run_evals.py,
│                       # setup_annotation_queue.py, snapshot_jobs.py, generate_fixture_*.py
├── data/               # cached_jobs.json, fixture_cvs/, fixture_linkedin/, labels/ (hand labels)
├── docs/               # architecture.md, opik_setup.md, extending_sources.md, jobvis.md, optimizing_latency.md
├── reports/            # baseline.json, tailor_batch.json, phase*_findings.md, phase2_eval_report.md
└── tests/              # 100+ tests (LLM mocked, network mocked, Opik off)
```

### 🔧 Essential Commands

```bash
make setup         # uv sync + pre-commit hooks
make app           # launch the Gradio app
make app-voice     # launch with the Jobvis voice extra
make batch         # baseline batch (prints projected cost; add --yes to run)
make tailor-batch  # Phase 2 tailoring batch (search + tailor per case)
make eval-datasets # push ranking + tailoring datasets to Opik from traces
make evals         # eval harness usage (extraction/ranking/tailoring/trajectory/calibration)
make queue         # create the Opik annotation queue + feedback definitions
make snapshot      # rebuild data/cached_jobs.json from live sources
make fixtures      # regenerate the synthetic fixture CVs + LinkedIn export ZIPs
make jobvis-agent  # create/update the Jobvis ElevenLabs agent (prints the agent id)
make test          # run the test suite
make lint          # ruff check
make format        # ruff format + fix
```

### 🎓 Target Audience

| Who | Why |
|-----|-----|
| **AI/ML Engineers** | Learn production agent architecture and LLMOps beyond tutorials |
| **Software Engineers** | Build an end-to-end LLM agent with observability baked in |
| **Data Scientists** | See how to measure an AI system honestly before optimizing it |

---

## 🛠️ Troubleshooting

- **No jobs / all `source: cache`** — you have no live-source keys, or the network is blocked. Expected; the cache is the offline fallback. Add Adzuna/JSearch keys and re-run `make snapshot`.
- **Cost shows $0.00** — you're on a free model (Groq/Ollama). Opik prices only OpenAI/Anthropic/Google models.
- **No traces in Opik** — check `OPIK_ENABLED=true` and that `OPIK_API_KEY` / `OPIK_WORKSPACE` are set. See [`docs/opik_setup.md`](docs/opik_setup.md).

---

## 💰 Cost

Reproducing everything in Part 1 costs **under $0.50** with API models, or is
**fully free** with local models (Ollama) or free tiers. The app runs with zero
keys via Remotive + the offline cache.

---

<div align="center">
  <h3>🎉 Ready to build an agent you can actually trust?</h3>
  <p><strong>Clone it, <code>make app</code>, and drop in a fixture CV.</strong></p>
  <p><em>Built with love by <a href="https://www.linkedin.com/in/shirin-khosravi-jam/">Shirin Khosravi Jam</a></em></p>
</div>

---

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=jamwithai/observable-job-agent&type=Date)](https://star-history.com/#jamwithai/observable-job-agent&Date)

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.
