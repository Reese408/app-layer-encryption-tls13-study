# AGENTS.md: Contributor and AI-Agent Rules

This file applies to every contributor, human or automated, who works in this repository.

## 1. Research integrity

1. **Never fabricate or estimate a measurement.** Every number in `reports/` or `README.md` must come from a file in `results/raw/` and be reproducible with the scripts in `analysis/`.
2. **Never claim a test or benchmark passed unless it was executed.** Report failures with their actual output.
3. **Never edit raw results.** `results/raw/<timestamp>/` is append-only. Corrections go into a new run, and the reason is recorded.
4. **Report blockers.** If a tool, model, or measurement is unavailable, record that in the report. Do not substitute guessed values.
5. Each run records its own environment metadata: git commit, tool versions, host details, and configuration.

## 2. Cryptographic rules

1. Use only the `cryptography` library's `AESGCM` primitive (or another established, audited library approved in review). **Never implement or modify cryptographic primitives.**
2. AES-256-GCM keys are exactly 32 bytes from a CSPRNG (`os.urandom` / `secrets`).
3. Every nonce is 96 bits from a CSPRNG, generated fresh for each encryption. Never derive a nonce from a counter shared across processes, from time, or from the payload.
4. Each key has a hard limit on how many messages it may encrypt, set well under the NIST SP 800-38D bound for random nonces (2³² invocations). Encryption fails closed when the limit is reached.
5. Requests and responses use separate keys, one for each direction.
6. Associated data (AAD) binds the HTTP method, the route, the key ID, and the envelope version.
7. Authentication failures return a generic error. The response must not reveal whether the failure was in the tag, the nonce, or the AAD.

## 3. Secrets and logging

1. Never log, print, or commit keys, plaintext payloads, ciphertext bodies, or certificate private keys.
2. Logs may contain only metadata: route, status, sizes, durations, and key ID.
3. `secrets/` and `certs/` are gitignored. Keys reach containers as Docker secrets or read-only mounts, never as environment variables, because those can leak through `docker inspect`.
4. Test fixtures use keys generated at test time, never committed key material.

## 4. Experimental validity

1. Both configurations, `plain` and `aead`, run **the same image, the same code path, and the same deterministic workload**. The only difference is the encryption step on the request and response bodies.
2. Container CPU/memory limits, payload sizes, concurrency, duration, warm-up, and repetition count are set in one configuration file and recorded with each run.
3. Benchmarks for the two configurations alternate in a randomized, recorded order, so drift (thermal, background load) does not systematically favor either one.
4. Load-generator CPU use is recorded so the client side can be ruled out as the bottleneck.

## 5. Scope control

Do not add infrastructure, services, dependencies, or features beyond those in `docs/plan.md` without approval from the repository owner.

## 6. Commands

| Task | Command |
|---|---|
| Set up environment | `make setup` |
| Run tests | `make test` |
| Start services | `make up` / `make down` |
| Smoke test | `make smoke` |
| Full benchmark | `make bench` |
| Analysis | `make analyze` |

## 7. Style

- Python 3.12, formatted and linted with `ruff`, with type hints on public functions.
- Documentation is formal and in the third person, and it cites standards (RFC 8446, NIST SP 800-38D) where relevant.
