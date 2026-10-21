# Research Notes: Implications for Frontier LLM and Agentic Systems

*Author's working notes, not paper text.* This study does **not** measure, and makes no claim about, the internal infrastructure of OpenAI, Anthropic or any other provider. Every statement here carries one of these labels:

| Label | Meaning |
|---|---|
| **[M]** | Measured on the local test system (Apple M5 Pro, Docker, FastAPI, Ollama with qwen3:8b Q4_K_M) |
| **[I]** | Mechanistic interpretation supported by this study's instrumentation (stage timers, Server-Timing, CPU counters) |
| **[E]** | Extrapolated from measured values, using stated assumptions |
| **[H]** | Hypothetical frontier-scale scenario, using only published architecture patterns ([references.md](references.md)) |
| **[U]** | Unknown; would need another experiment |

Numbers marked *H5 pending* will be filled in from `results/processed/<infer dir>/h5.md` once the H5 run is complete. They must not be estimated in advance.

## 1. Inference-heavy requests

- **[M] Experiment A.** At 1 KiB and one VU, the envelope added +0.015 ms to median request latency, +13% in relative terms (reports/results.md §1).
  - It adds a cost that is roughly fixed per request and grows with payload size: H4 slopes of 0.16–8.05 ms per payload doubling across the tested range.
  - It adds 18–53% server CPU per request against a deliberately cheap workload (0.02–2 ms of compute).
- **[M] Experiment C pipeline checks** (smoke and determinism runs, not H5 data):
  - generation runs at about 52 tokens/s;
  - a 32-token request takes about 0.64 s and a 256-token request about 5 s;
  - the instrumented encryption time (client and server) was about 0.09% of the 32-token request and 0.008% of the 256-token request.
  - *H5 pending:* the pre-registered differential test of whether the overhead share stays below 1%.
- **[I]** At these sizes, both the absolute envelope cost (well under 1 ms for 1 KiB prompts) and the generation cost (milliseconds per token) are nearly independent of mode. The relative share therefore falls roughly as 1/latency. The worst case for any threshold claim is the *shortest* response, which is why H5 tests 32 tokens.
- **[E]** If the per-request envelope cost is *c* and inference latency is *T*, the share is ≈ *c*/*T*. For *c* below 1 ms and *T* of hundreds of milliseconds to seconds, which is typical of interactive generation, the share is small. This assumes envelope cost is independent of model size, which holds because the envelope processes bytes, not tokens.
  - **Exception:** very large prompts, such as long-context retrieval-augmented generation (RAG) with hundreds of KiB of documents, push *c* up. Experiment A's 256 KiB cell cost about +1.3 ms per request at 1 VU, and more under load.

## 2. Agentic workflows: many small calls

- **[H]** Agent loops interleave model calls with tool calls, retrieval and service-to-service requests [13]. Tool protocols such as MCP send many small JSON-RPC messages per task, over stdio or HTTP [14].
- **[E]** The per-hop costs add up along a *sequential* chain. Using Experiment A's measured order of magnitude, about 0.015–0.1 ms of request latency per small-payload hop plus the client's own crypto, a 100-step sequential workflow accumulates roughly 1.5–10 ms. That is still small next to even one model call of hundreds of milliseconds. The assumptions: payloads of a few KiB or less, a warm connection, hardware AES, and this stack's Python and JSON overhead.
- **[I]** The larger agentic cost is *structural*, not cryptographic:
  - Every service that must read a payload needs keys. A tool server that decrypts, acts and re-encrypts is a new endpoint, inside the trust boundary.
  - Envelopes terminated hop by hop recreate the TLS-termination problem at the application layer.
  - End-to-end designs need per-recipient keys, for example HPKE [3].
- **[U]** How overhead compounds when agent steps run in parallel, or when tool payloads are large (documents, images), was not measured.

## 3. High-concurrency serving

- **[M]** In Experiment A, the envelope's CPU cost lowered throughput by 16–61% and raised p99 latency in all 45 closed-loop pairs. In Experiment B, at a fixed sub-capacity arrival rate, it raised p99 by 20–49%. That workload was CPU-bound and cheap, the opposite of LLM serving.
- **[H]** Production LLM servers batch requests at the iteration level [10] and manage KV-cache memory carefully [9]. Latency there is dominated by GPU scheduling, prefill and decode [11]. Envelope work runs on CPU in the API or gateway tier, not on the GPU.
- **[E/I]** The envelope therefore competes for *front-end CPU* (gateways, API servers), not for *GPU capacity*. Experiment A's 18–53% CPU increase per request is an upper bound for a tier that does almost nothing else.
  - In a tier that also authenticates, rate-limits, tokenizes and logs, the relative increase would be smaller.
  - The absolute CPU added per request is the transferable quantity. It still needs provisioning at scale.
- **[U]** Concurrency 4 was deferred (G4). Whether interleaved GPU queueing changes the overhead share at higher load is untested.

## 4. Streaming responses

- **[D]** The study's envelope is **whole-message**. The server can only seal the response once generation is complete, and the client can only verify it once the whole message has arrived. Used as is with token streaming, it would remove streaming: time to first token would become time to last token.
- **[S/H]** Streaming needs per-chunk authenticated encryption, for example a record-based scheme like RFC 8188's `aes128gcm` [6].
  - Each chunk needs its own nonce, tag and sequence binding, so that chunks cannot be reordered, dropped or truncated undetected. The final chunk must be marked, to resist truncation.
  - That costs 16 bytes of tag per chunk plus framing, and per-chunk encryption calls. With single-token chunks, this is a far larger relative overhead than whole-message encryption.
  - Server-sent event framing must carry the binary records, which means Base64 again.
- **[I]** Chunk size trades latency against overhead. Coalescing tokens into chunks of, for example, 50–100 ms reduces crypto calls but delays the first token.
- **[U]** Time to first token was **not measured** (non-streaming design; research-design.md §7.4). A streaming variant would be a separate experiment.

## 5. Security versus operational cost

See [security-analysis.md](security-analysis.md) §5. In brief:
- **[J]** The layer is justified when bodies cross TLS-terminating infrastructure that the data owner does not trust, *and* when the envelope terminates where the data is actually used. For LLM serving, that means at or inside the inference service, possibly in an attested environment, as in Apple's published Private Cloud Compute design [12].
- **[J]** It is hard to justify for a single trusted hop, where it adds CPU, latency, debugging difficulty and key-management risk without changing who can read the data.

## 6. What the paper may and may not say

- **May:** "On the test system, the envelope's added latency was X% (CI …) of a 32-token inference request." *(H5 pending)* The paper may also extrapolate, under the stated assumptions of §1–3, to the conditions under which the share stays small.
- **May not:**
  - "Encryption is negligible in ChatGPT or Claude." Their infrastructure was not measured.
  - "Encryption has no effect at scale." Concurrency was not tested.
  - "The envelope works with streaming." It does not without redesign.
