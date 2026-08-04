.DEFAULT_GOAL := help

# Self-documenting help: any target with a `## comment` is listed.
.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: setup
setup: ## Install deps and pre-commit hooks
	uv sync --all-groups
	uv run pre-commit install

.PHONY: app
app: ## Launch the Gradio app
	uv run python -m job_scout.app

.PHONY: jobvis
jobvis: ## Serve the Jobvis voice console + API on :8000 (build it first with `make web-build`)
	uv run python -m job_scout.api

.PHONY: web-build
web-build: ## Build the Jobvis console into web/out (static export served by `make jobvis`)
	cd web && npm ci && npm run build

.PHONY: web-dev
web-dev: ## Next dev server on :3000 against the API on :8000 (run `make jobvis` too)
	cd web && npm run dev

.PHONY: batch
batch: ## Run the baseline batch (prompts for --yes cost confirmation)
	uv run python scripts/run_batch.py

.PHONY: snapshot
snapshot: ## Rebuild data/cached_jobs.json from live sources
	uv run python scripts/snapshot_jobs.py

.PHONY: fixtures
fixtures: ## Regenerate the synthetic fixture CV PDFs + LinkedIn export ZIPs
	uv run python scripts/generate_fixture_cvs.py
	uv run python scripts/generate_fixture_linkedin.py

.PHONY: tailor-batch
tailor-batch: ## Run the Phase 2 tailoring batch (prompts for --yes cost confirmation)
	uv run python scripts/run_tailor_batch.py

.PHONY: eval-datasets
eval-datasets: ## Push ranking + tailoring datasets to Opik from traces
	uv run python scripts/build_eval_dataset.py --kind ranking --push
	uv run python scripts/build_eval_dataset.py --kind tailoring --push

.PHONY: evals
evals: ## Show the eval harness usage (each suite prompts for --yes)
	uv run python scripts/run_evals.py --help

.PHONY: queue
queue: ## Create the Opik annotation queue + feedback definitions
	uv run python scripts/setup_annotation_queue.py --queue

.PHONY: jobvis-agent
jobvis-agent: ## Create/update the Jobvis ElevenLabs agent (prints the agent id)
	uv run python scripts/setup_jobvis_agent.py

.PHONY: test
test: ## Run the test suite
	uv run pytest

.PHONY: lint
lint: ## Lint with ruff
	uv run ruff check .

.PHONY: format
format: ## Format with ruff
	uv run ruff format .
	uv run ruff check --fix .

.PHONY: gates
gates: ## Deterministic eval regression gate (Opik dataset access, zero LLM calls)
	uv run pytest gates/ -v
