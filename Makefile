.PHONY: setup lint test up down validate smoke pilot validate-pipeline bench openloop analyze stats check-run

PY := uv run python

setup:            ## venv, local CA/certs, AEAD keys, payload fixtures, API image
	uv sync --python 3.12
	./scripts/gen_certs.sh
	$(PY) scripts/gen_keys.py
	$(PY) scripts/gen_payloads.py
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

analyze:          ## RUN=results/raw/<dir> (defaults to latest)
	$(PY) analysis/aggregate.py $(RUN)

stats:            ## RUN=results/raw/<dir>: paired run-level inference
	$(PY) analysis/stats.py $(RUN)

check-run:        ## RUN=results/raw/<dir>: acceptance criteria for the revised pipeline
	$(PY) analysis/validate_pipeline.py $(RUN)
