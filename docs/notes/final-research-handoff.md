# Final Research Handoff (Evidence Inventory)

This file is an inventory of verified evidence for the student researcher. **It is not paper text.** Every number in it comes from a report listed below, and each of those reports derives its numbers from `results/raw/` through `analysis/`. State of the evidence: branch `phase-4-inference`, verified on 2026-10-21.

## 1. Completed experiments and sources

| Exp. | Question | Raw data (commit, tree) | Report | Valid runs |
|---|---|---|---|---|
| A | Closed-loop latency, throughput, CPU and memory (H1–H4) | `results/raw/20261020T233709Z-full/` (`49c57b7`, clean) | [reports/results.md](../../reports/results.md) §3–4 | 90/90 |
| B | Open loop at fixed arrival rates (secondary) | `results/raw/20261021T013349Z-openloop/` (`49c57b7`; manifest *dirty*: only documentation and Makefile changed during the run, results.md §2.4) | results.md §5 | 30/30 |
| C | LLM inference (H5): local `qwen3:8b`, interleaved | `results/raw/20261021T155308Z-infer/` (`24bf4fd`, clean). Pilot and checks: `…145225Z-infer-pilot`, `…145025Z-infer-determinism`, `…144808Z-infer-smoke` (`d70823c`, clean) | [reports/results-inference.md](../../reports/results-inference.md) §2; [reports/infer-pilot.md](../../reports/infer-pilot.md) | 10/10 (pilot 6/6) |
| D | Envelope cost breakdown (A1 microbenchmark) | `results/raw/20261021T162323Z-envelope-micro/` (`f8d0d9c`, clean) | results-inference.md §3 | 5 processes × 25 repetitions |

**Pre-registration:** research-design.md §5 (A and B, fixed before Experiment A), §7 (H5, commit `fcfdcee`, before any H5 data), and §8 (D, commit `24bf4fd`, before D data).

**Excluded data, kept and documented:**
- the aborted first launch of Experiment A, `results/raw/20261020T233530Z-full/` (results.md §2.4);
- the validation datasets (pilot.md §10.3).

**Raw-data integrity (checked 2026-10-21):** `git log` over `results/raw/` shows 826 additions, 3 modifications and no deletions. All 3 modifications are appends:
- `20261020T184550Z-validate-closed/resources.csv` (+396 rows) and `runs.csv` (+9 rows), in commit `08b9745`;
- `20261020T173307Z-validate-closed/resources.csv` (+615 rows), in commit `7392057`.

In each case a validation dataset had been committed while its run was still in progress, and the remaining rows were committed later. No line of any raw file has ever been removed or changed, and no Experiment A–D file has been modified.

## 2. Hypothesis outcomes and main numbers

| Hypothesis | Outcome | Key numbers | Source |
|---|---|---|---|
| H1 median latency higher | Supported, 9/9 cells (Holm) | +13.0% to +52.3% (geometric-mean ratio); +0.015 ms (1 KiB, 1 VU) to +65 ms (256 KiB, 64 VU) | results.md §1, §3 |
| H2 tail latency higher | Partly supported: p99 higher in all 45 block pairs, 5/9 cells significant | p99 +14.9% to +55.3% | results.md §3 |
| H3 throughput lower | Supported, 9/9 | −15.7% to −61.0%. At 1 VU this includes client-side cryptography. | results.md §2.3, §3 |
| H4 Δ grows with size | Supported at 1, 16 and 64 VU, within the tested range (1–256 KiB) | 0.16 / 1.99 / 8.05 ms per doubling | results.md §3.4 |
| Server CPU per request (secondary) | Higher, 9/9 | +18.4% to +52.8% | results.md §3.5 |
| Peak memory (secondary) | No significant difference, 0/9; **equivalence not established** | −1.8% to +5.6% | results.md §3.5 |
| E2E median, including client crypto (secondary) | Higher, 9/9 | +19.4% to +160.3% | results.md §1 |
| Experiment B, open loop | p99 higher at all sizes; p50 higher at 1 KiB and 256 KiB; **16 KiB anomaly** (plaintext p50 higher), unresolved | p99 +20% to +49% | results.md §5 |
| **H5** overhead share < 1% under inference | **Supported at 32 and 256 tokens** | One-sided 95% UCB **0.358%** and **0.645%**; estimates −0.458% and +0.093% | results-inference.md §2.2 |
| D: what the envelope cost is | Descriptive | AES-GCM 7.1–12.9% of seal/open time; Base64 + JSON 78.1–93.5% | results-inference.md §3 |

## 3. Expected versus observed (as already documented)

- **Expectation recorded beforehand** (research-design §2): AES-GCM would be cheap, and any measurable overhead would come from Python envelope handling (JSON and Base64). **Observed:** Experiment D confirms this. AES-GCM is 7–13% of the envelope cost, and Base64 and JSON are 78–94%.
- **Pilot observation** (pilot.md, point 4): the relative overhead is large because the baseline workload is cheap; against inference it would be a much smaller share. **Observed:** H5. The same envelope is under 0.65% of inference latency, against +13% to +52% for the cheap workload.
- **Not expected:**
  - the 16 KiB open-loop median, where plaintext was slower, cause unresolved (results.md §5.3);
  - H5 noise about 10 times the pilot's, attributed to GPU-sharing desktop activity on the host (results-inference.md §2.3);
  - k6 v2.3 silently ignoring `minVersion`, documented and with no effect on results (plan.md deviation 3).

## 4. Security and performance trade-offs supported by the evidence

- **Performance (measured).** The cost is roughly fixed per request and grows with payload size: about 0.5 ms per request at about 1 KiB (results-inference.md §2.3), up to 1.3 ms at 256 KiB and 1 VU (results.md). Whether that matters depends on the rest of the request: it is large next to sub-millisecond work and negligible next to inference.
- **Security (design properties, not tested; [security-analysis.md](security-analysis.md)).** The envelope adds confidentiality and integrity against TLS-terminating intermediaries and logging middleware *only if* those components do not hold the keys. It does not protect the endpoints, metadata (sizes and timing), or same-route replay. It adds key-management and nonce-management burdens.
- **Format matters more than the cipher (Experiment D).** A binary or record-based envelope would be expected to cost less. That is a hypothesis, not a measurement.

## 5. Limitations and unresolved questions

- **Environment.** One host (Apple M5 Pro, macOS, Docker Desktop VM), a loopback network, a single Uvicorn worker, Python. There was no real intermediary, so the cost of the countermeasure was measured, not its benefit.
- **Statistics.** *n* = 5 blocks per cell. Memory equivalence was not established. H2 has wide intervals at 1 KiB. The t procedures assume roughly normal log ratios, which cannot be checked at *n* = 5.
- **H5.**
  - Non-streaming, so no time to first token.
  - Concurrency 1 only.
  - One quantized 8B model, with prompts of about 1 KiB.
  - The host was in interactive use during the run.
  - The prompt is plaintext on the API-to-model hop.
  - API CPU per request is dominated by the container health check.
- **Experiment D.** A microbenchmark (warm caches). The client was not broken down. No alternative envelope was measured.
- **Unresolved.** The 16 KiB open-loop anomaly. The TLS record-boundary hypothesis is untested.

## 6. LLM and agentic implications (details: [frontier-implications.md](frontier-implications.md))

| Type | Claim |
|---|---|
| **Measured** | Local 8B inference with 32–256-token responses: envelope share < 1% (UCB 0.358% / 0.645%). Envelope time about 0.45–0.53 ms per request. |
| **Measured** | Cheap API requests: +13% to +52% median latency, and up to −61% throughput at 1 VU (client crypto included). |
| **Inferred** (mechanism from instrumentation) | The share scales roughly as the fixed envelope cost divided by request latency. Longer or slower inference gives a smaller share; large prompt payloads give a larger absolute cost. |
| **Inferred** | Envelope cost falls on front-end CPU (API or gateway tier), not on the GPU. |
| **Hypothetical** | Sequential agent chains of many small calls accumulate per-hop costs, of the order of milliseconds per 100 hops at Experiment A magnitudes. Streaming needs per-chunk authenticated encryption, such as RFC 8188-style records, which the current whole-message envelope does not support. |
| **Not supported** | Any statement about the infrastructure of ChatGPT, Claude or other providers. None was measured. |

## 7. Figure inventory

| Figure | Files | Source data | Status |
|---|---|---|---|
| H5 inference overhead | `reports/figures/fig_h5_inference_overhead.{svg,png}` | `results/processed/20261021T155308Z-infer/h5.csv`, `h5_runs.csv` | New, publication style (grayscale, 600 DPI, deterministic) |
| Envelope cost breakdown | `reports/figures/fig_envelope_cost_breakdown.{svg,png}` | `results/processed/20261021T162323Z-envelope-micro/breakdown.csv` | New, publication style |
| Experiments A/B static dashboard (forest plots, per-run dots, ECDFs) | `results/processed/dashboard/index.html` (`make dashboard`) | stats and sample outputs | Existing; not in paper style |
| Grafana dashboards (Experiments A/B) | `make grafana` | OpenMetrics backfill | Existing; post hoc only |

Captions and reading notes: [reports/figures/README.md](../../reports/figures/README.md).

## 8. Reproduction

```
make setup                 # venv, certificates, keys, fixtures, image (no measurements)
make test                  # 86 tests (2026-10-21)
make analyze RUN=results/raw/20261020T233709Z-full && make stats RUN=results/raw/20261020T233709Z-full
make analyze RUN=results/raw/20261021T013349Z-openloop && make stats RUN=results/raw/20261021T013349Z-openloop
make h5 RUN=results/raw/20261021T155308Z-infer
uv run python analysis/envelope_micro.py results/raw/20261021T162323Z-envelope-micro
make figures
```

Re-running the experiments themselves (`make bench`, `make openloop`, `make infer`, `scripts/envelope_micro.py`) is neither needed nor authorized for the paper.

## 9. Optional future research (not started; each needs approval)

1. **Binary envelope (A2).** Compare the JSON/Base64 envelope with an equivalent binary or record-based one (for example RFC 8188-style), to test whether the about 80–94% formatting cost disappears.
2. **Streaming inference.** Per-chunk sealing; measure time to first token and the overhead per chunk.
3. **Concurrency under inference.** Several VUs against Ollama, with batching effects controlled.
4. **The 16 KiB anomaly.** Payloads straddling the 16 KiB TLS record boundary, with TLS-record instrumentation.

## 10. Pull-request sequence (prepared, not executed)

- **PR #1** (`phase-2-minimum-experiment` → `main`): Experiments A and B. Still a draft.
- **Phase 4 PR (to be opened after owner approval):** `phase-4-inference` → **`phase-2-minimum-experiment`**. Phase 4 is built on top of Phase 2, so a PR against `main` would repeat all of PR #1's changes. Against `phase-2-minimum-experiment`, the diff contains only Phase 4.
- **Sequence:**
  1. The owner signs off results.md and merges PR #1.
  2. Retarget the Phase 4 PR to `main`. GitHub does this automatically if the `phase-2-minimum-experiment` branch is deleted on merge.
  3. Review and merge the Phase 4 PR.
- **Proposed PR title:** "Phase 4: LLM inference (H5), envelope cost breakdown, figures, and research notes". A proposed description is in the final summary to the owner.
