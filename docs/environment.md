# Environment Inventory

Recorded on **2026-10-20** during Phase 1 by running the commands shown below. Each benchmark run also writes its own machine-generated manifest. This document is the baseline.

## Host

| Item | Value | Source |
|---|---|---|
| OS | macOS 26.6 (build 25G72) | `sw_vers` |
| Architecture | arm64 | `uname -m` |
| CPU | Apple M5 Pro, 18 cores | `sysctl machdep.cpu.brand_string hw.ncpu` |
| Memory | 64 GiB | `sysctl hw.memsize` |

## Tooling

| Tool | Version | Status | Source |
|---|---|---|---|
| Docker Engine (Docker Desktop) | client 29.4.0 / server 29.8.1 | Available | `docker --version`, `docker info` |
| Docker Desktop VM | 18 vCPU, ~7.7 GiB RAM | Available | `docker info` |
| Docker Compose | v5.5.1 | Available | `docker compose version` |
| Python | 3.14.7 (default), 3.12.14 | Available. **3.12 chosen** for library compatibility. | `python3 --version`, `python3.12 --version` |
| uv | 0.12.19 | Available | `uv --version` |
| OpenSSL | 3.6.4 | Available | `openssl version` |
| k6 | — | **Not installed on host.** The plan is to use the `grafana/k6` container image, pinned by digest. | `k6 --version` |
| Ollama | 0.35.0 | Running on `localhost:11434` | `ollama --version`, `/api/version` |
| Qwen models | `qwen3:8b` (8.2B, Q4_K_M), `qwen3-coder:30b` | Available | `ollama list`, `ollama show` |
| GitHub CLI | 2.101.0, authenticated | Available | `gh auth status` |

## Implications for the design

- **Docker Desktop VM memory (~7.7 GiB)** limits container memory. The API services will be capped well below this, for example at 1 GiB each. Ollama runs outside the VM.
- **k6 in a container** shares the Docker Desktop VM's CPUs with the API containers. CPU limits (`cpus:`) will be set so the load generator and the services under test do not compete without control, and load-generator CPU will be recorded.
- **`qwen3:8b`** is the default inference model for Phase 4 because its size allows repeatable runs. Its default `temperature` (0.6) and default *thinking* mode will be overridden (`temperature=0`, `think=false`, fixed `seed`) for determinism.
