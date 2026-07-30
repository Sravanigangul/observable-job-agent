# Job Scout — Architecture

![Job Scout architecture](images/architecture_part2.png)

Grounded in the current code (`src/job_scout/…`). Renders anywhere Mermaid is
supported (GitHub, most blog engines). Legend: **solid arrows** = data flow,
**dotted arrows** = cross-cutting concerns (LLM calls, Opik tracing, config).

The diagram above is also available as editable Mermaid source:

```mermaid
flowchart TB
  U(["User"]) -->|"upload CV (PDF)"| UI

  subgraph UI_L["Gradio UI · app.py"]
    UI["4-step wizard<br/>Resume → Profile → Jobs → Tailor<br/>streamed status · fit gauges · footer"]
  end

  UI -->|"filepath"| CVR["cv_reader.py<br/>pypdf: PDF → text"]
  CVR -->|"CV text"| EP["profile.py · extract_profile<br/>1 LLM call · structured output"]
  EP -->|"Profile"| RUN

  subgraph ORCH["runner.py · orchestrator (UI + batch share it)"]
    RUN["stream_search / run_once / stream_tailor<br/>measures cost + latency · streams node status"]
  end

  RUN -->|"invocation A: Profile<br/>invocation B: selected_job_id only"| G

  subgraph G["ONE LangGraph agent · graph.py (shared MemorySaver, process lifetime)"]
    direction TB
    S((START)) --> RE{"route_entry<br/>selected_job_id set?"}
    RE -->|"no → job search"| FJ
    FJ["fetch_jobs<br/>LLM picks search args via tool call"] --> RJ
    RJ["rank_jobs<br/>batched LLM scoring · BATCH_SIZE=5"] --> D{"enough good matches?<br/>≥5 jobs scoring ≥60"}
    D -->|"no · under 2 loops"| RQ["reformulate_query<br/>broaden the query"]
    RQ --> FJ
    D -->|"yes · or cap hit"| E((END))
    RE -->|"yes → tailoring (reads profile +<br/>ranked_jobs from the checkpoint)"| TL
    TL["tailor<br/>corpus-grounded pack · 1 LLM call"] --> VT
    VT["validate_tailoring<br/>deterministic fabrication check · 0 LLM"] --> E
  end

  CORP["corpus.py · CandidateCorpus<br/>CV segmentation + optional LinkedIn export ZIP"] --> TL
  CORP --> VT
  TL -->|"TailoringPack"| REND["renderer.py<br/>Jinja2 → LaTeX → PDF (tectonic,<br/>degrades to .tex + Overleaf)"]

  FJ -->|"query · country · remote"| SRCH
  subgraph SRCH["run_search cascade · jobs_api.py (fall-through, keyless-safe)"]
    direction LR
    JS["JSearch<br/>primary"] --> AZ["Adzuna<br/>international"] --> RM["Remotive<br/>keyless"] --> CA["Cache<br/>~247 offline jobs"]
  end
  SRCH -->|"JobPostings"| RJ

  E -->|"RankedJobs"| RUN
  RUN -->|"ranked cards + cost/latency + Opik link"| UI

  subgraph LLM_L["LLM · llm.py (get_chat_model + ensure_budget ≤25)"]
    OA["OpenAI gpt-4o-mini<br/>(or Groq / Ollama via SCOUT_MODEL)"]
  end
  EP -. "LLM" .-> OA
  FJ -. "LLM" .-> OA
  RJ -. "LLM" .-> OA
  RQ -. "LLM" .-> OA
  TL -. "LLM (SCOUT_TAILOR_MODEL, temp 0.3)" .-> OA

  subgraph OBS["Opik observability · tracing.py"]
    TR["track_langgraph + OpikTracer<br/>span tree · agent graph · per-run cost"]
    PL["prompt library<br/>3 prompts, versioned"]
    AT["CV attached to trace"]
  end
  RUN -. "wrap + traces" .-> TR
  G -. "spans" .-> TR
  EP -. "register" .-> PL
  CVR -. "PDF" .-> AT

  CFG["config.py · Settings<br/>SecretStr keys · SCOUT_MODEL · budget=25"] -. "config" .-> RUN

  classDef llm fill:#e8f0fe,stroke:#4285f4,color:#111;
  classDef obs fill:#e6f4ea,stroke:#137333,color:#111;
  classDef node fill:#fff7e6,stroke:#b06000,color:#111;
  class OA,LLM_L llm;
  class TR,PL,AT,OBS obs;
  class FJ,RJ,RQ,TL,VT node;
```

## Reading it

1. **Upload → text → profile.** The Gradio wizard hands the PDF to `cv_reader`
   (pypdf), then `extract_profile` turns the text into a typed `Profile` with one
   structured-output LLM call — *before* the graph, so it's extracted once.
2. **The agent graph.** `runner.py` feeds the profile into the LangGraph:
   `fetch_jobs` (the LLM chooses the search arguments) → `rank_jobs` (batched fit
   scoring) → a conditional edge that either loops through `reformulate_query`
   (max 2) to broaden the search, or ends.
3. **Job sources.** `fetch_jobs` calls the `run_search` cascade — JSearch →
   Adzuna → Remotive → offline cache — each tried only if the previous returned
   too few, so it runs with **zero API keys**.
4. **The tailoring pipeline (Phase 2).** The SAME compiled graph gains a
   conditional entry: when the caller passes only `selected_job_id`, the
   `route_entry` router sends the invocation to `tailor`, which reads `profile`,
   `ranked_jobs`, and `cv_text` from the thread's **checkpoint** — nothing
   re-runs. `tailor` selects and rewords items from the `CandidateCorpus` (CV +
   optional LinkedIn data export), then `validate_tailoring` deterministically
   checks every bullet, skill, and factual cover-letter sentence against the
   corpus (`fabrication_flags` — logged, never retried). The pack renders to a
   one-page PDF via Jinja2 → LaTeX → tectonic, degrading to a `.tex` + Overleaf
   pointer when tectonic is absent.
5. **One graph, one checkpointer.** `get_compiled_graph()` holds a single
   compiled graph + `MemorySaver` for the process lifetime, so the tailor
   invocation lands on the search invocation's thread. That makes the "second
   invocation, same thread, no recomputation" trace possible — and makes
   explicitly nulling `selected_job_id` on every search invocation mandatory
   (the runner does; the phase 2 notebook demonstrates the stale-state bug).
6. **Cross-cutting (dotted).** Every node's LLM call goes through `llm.py`
   (provider-agnostic + a per-run call budget; the budget is per-THREAD now that
   search and tailor share a checkpoint). **Opik** traces every run: span tree,
   the auto-drawn agent graph (per-run tracer via `trace_graph`), per-run cost,
   the versioned prompt library (4 prompts), and the CV attached to the trace.
   `config.py` supplies keys and settings.
