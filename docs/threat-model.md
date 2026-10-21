# Threat Model

## 1. System and assets

| Asset | Description |
|---|---|
| A1: Request payload | Client-submitted data, such as a prompt or a business record. Confidentiality and integrity both matter. |
| A2: Response payload | Server-computed output, including LLM completions. Confidentiality and integrity both matter. |
| A3: AEAD keys | Two 32-byte AES-256-GCM keys, one for each direction. |
| A4: TLS private key | The server certificate's private key. |

## 2. Trust boundaries

```
[Client app] ══TLS══▶ [TLS terminator: LB / gateway / sidecar] ──(TLS or plaintext)──▶ [API app] ──▶ [LLM]
     ▲                         ▲ untrusted or partially trusted                          ▲
  AEAD endpoint                    intermediary zone                               AEAD endpoint
```

In this laboratory setup, k6 connects **directly** to the API over TLS 1.3. No intermediary exists. The intermediary zone is part of the *motivating* model and is not emulated, so this study measures the cost of the countermeasure, not its effectiveness in deployment.

## 3. Adversaries and properties

| Adversary | TLS 1.3 alone | TLS 1.3 + app-layer AEAD |
|---|---|---|
| Passive network observer between hops | Protected | Protected |
| Active network attacker (MITM without a trusted certificate) | Protected | Protected |
| Compromised or curious TLS-terminating intermediary (LB, CDN, gateway, mesh sidecar) | **Not protected**: sees plaintext | Protected: sees only the envelope |
| Plaintext body captured by request logging or APM middleware outside the app | **Not protected** | Protected (ciphertext only) |
| TLS misconfiguration (downgrade, verification disabled on one hop) | Not protected | Payload still protected, if keys are not exposed |
| Cross-route replay / response reflection | N/A | Mitigated by AAD binding and separate per-direction keys |
| Same-route replay of a captured envelope | Not protected at this layer | **Not mitigated** in the minimum experiment (see §5) |
| Compromised client or server endpoint | Not protected | Not protected: keys and plaintext live there |
| Traffic analysis (sizes, timing) | Not protected | Not protected: AES-GCM preserves length |
| Denial of service | Out of scope | Out of scope. Decryption adds per-request CPU cost. |

## 4. Security requirements implemented

1. **R1:** Only audited AEAD from the `cryptography` library, AES-256-GCM, with 128-bit tags.
2. **R2:** A unique random 96-bit nonce per encryption and a per-key message ceiling (NIST SP 800-38D).
3. **R3:** A separate key for each direction, plus AAD binding of version, direction, method, route, and key ID.
4. **R4:** Fail closed with a generic error on any authentication failure.
5. **R5:** No key, plaintext, or ciphertext in logs. Keys are delivered as read-only mounted secrets.
6. **R6:** TLS 1.3 only, with certificate verification enabled on the client.

## 5. Known limitations (accepted for the experiment)

- **Static symmetric keys.** The study has no key agreement, key rotation, or KMS/HSM. A production design would derive per-session keys, for example with HPKE (RFC 9180), and rotate them.
- **No same-route replay defense.** Real deployments would add a timestamp or sequence number inside the plaintext and a server-side nonce cache.
- **Shared-secret client.** The load generator holds the client keys. This is acceptable for measurement but does not represent a real key-distribution model.
- **Inference hop (Phase 4).** The API forwards the decrypted prompt to Ollama over plain HTTP on the same host, and receives the completion in plaintext. The envelope protects the path from client to API only; the model necessarily processes plaintext. A deployment that needs protection up to the model would have to place the envelope endpoint inside the inference service, or run that service in a trusted execution environment. Neither is evaluated here.
- **Instrumentation metadata.** `Server-Timing` reveals stage durations to anything on the path, in both modes. This is acceptable for the experiment, but production systems should remove such headers. In particular, an inference duration can reveal the length of the response.
- **Metadata is visible.** Envelope size, `kid`, route, and timing remain observable to intermediaries.
