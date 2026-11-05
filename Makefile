.PHONY: setup lint test up down validate smoke pilot validate-pipeline bench openloop infer-determinism infer-pilot infer h5 figures analyze stats check-run dashboard grafana grafana-down

PY := uv run python

setup:            ## venv, local CA/certs, AEAD keys, payload fixtures, API image
	uv sync --python 3.12
	./scripts/gen_certs.sh
	$(PY) scripts/gen_keys.py
	$(PY) scripts/gen_payloads.py
	$(PY) scripts/gen_prompts.py
	docker compose build
	docker compose pull k6

lint:
	uv run ruff check app tests scripts analysis
	uv run ruff format --check app tests scripts analysis

test:             ## unit, functional, cryptographic and TLS tests
	uv run pytest

up:
	docker compose up -d --wait api-plain api-aead

down:
	docker compose down

validate: up      ## TLS negotiation + resource-sampler validation evidence
	$(PY) scripts/validate_env.py

smoke: up         ## short run, fails on any error or integrity mismatch
	$(PY) scripts/run_matrix.py --profile smoke

pilot: up
	$(PY) scripts/run_matrix.py --profile pilot

validate-pipeline: up   ## reduced closed- and open-loop runs exercising R1/R3/R6
	$(PY) scripts/run_matrix.py --profile validate-closed
	$(PY) scripts/run_matrix.py --profile validate-open

bench: up         ## full 90-run closed-loop matrix (requires explicit confirmation flag)
	$(PY) scripts/run_matrix.py --profile full --allow-full

openloop: up      ## separate open-loop experiment, 30 runs (requires confirmation flag)
	$(PY) scripts/run_matrix.py --profile openloop --allow-full

infer-determinism: ## Experiment C: model determinism check (direct to host Ollama)
	$(PY) scripts/run_infer.py --determinism

infer-pilot: up   ## Experiment C pilot: 3 blocks x 2 lengths, interleaved modes
	$(PY) scripts/run_infer.py --profile infer-pilot

infer: up         ## Experiment C (H5) full run, 5 blocks (owner gate G7; confirmation flag)
	$(PY) scripts/run_infer.py --profile infer --allow-full

h5:               ## RUN=results/raw/<dir>: Experiment C acceptance criteria and H5 analysis
	$(PY) analysis/h5.py $(RUN)

figures:          ## publication figures (grayscale SVG + 600 DPI PNG) from results/processed
	cd reports/figures && uv run --project ../.. python fig_h5_inference_overhead.py
	cd reports/figures && uv run --project ../.. python fig_envelope_cost_breakdown.py

analyze:          ## RUN=results/raw/<dir> (defaults to latest)
	$(PY) analysis/aggregate.py $(RUN)

stats:            ## RUN=results/raw/<dir>: paired run-level inference
	$(PY) analysis/stats.py $(RUN)

check-run:        ## RUN=results/raw/<dir>: acceptance criteria for the revised pipeline
	$(PY) analysis/validate_pipeline.py $(RUN)

dashboard:        ## RUNS="results/raw/<dir> ..." -> results/processed/dashboard/index.html
	$(PY) analysis/dashboard.py $(RUNS)

OBS = docker compose -f observability/compose.yaml
grafana:          ## RUNS="results/raw/<dir> ..." -> backfilled Prometheus + Grafana on http://127.0.0.1:3000 (never during a benchmark)
	@if docker ps --format '{{.Names}}' | grep -q '^aead-study-k6-'; then echo "a benchmark is running; refusing to start Grafana"; exit 1; fi
	$(PY) analysis/export_prometheus.py $(RUNS)
	$(OBS) down
	rm -rf results/processed/grafana/tsdb
	$(OBS) --profile backfill run --rm promtool
	$(OBS) up -d --wait prometheus grafana
	@echo "Grafana: http://127.0.0.1:3000  (Prometheus: http://127.0.0.1:9090)"

grafana-down:     ## stop the post-hoc observability stack
	$(OBS) down
