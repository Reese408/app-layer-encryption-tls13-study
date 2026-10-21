# Research Notes: What Application-Layer AES-GCM Protects

*Author's working notes, not paper text. Evidence labels:* **[M]** measured in this study; **[D]** follows from the design or the code (verifiable by inspection or tests, not by an attack experiment); **[S]** from a standard or published source (see [references.md](references.md)); **[J]** engineering judgment.

## 1. Scope of the evidence

This study measured the **cost** of the envelope. It did not attack anything, so it provides **no measurement of security benefit**. The security statements below describe properties of the design (**[D]**) or of the primitives (**[S]**). They are not experimental findings. No intermediary was emulated (threat-model.md §2). In the paper, these points should be stated as properties under stated assumptions, never as "the experiment showed that the system is more secure."

## 2. What the envelope adds beyond TLS 1.3

TLS 1.3 protects each hop between two TLS endpoints [RFC 8446] **[S]**. Wherever TLS is terminated, for example at a CDN, load balancer, API gateway, mesh sidecar, logging or APM middleware, or a message queue, the body is plaintext to that component. The envelope changes that only for components that **do not hold the envelope keys** **[D]**:

| Property | Holds when | Basis |
|---|---|---|
| Body confidentiality against TLS-terminating intermediaries | The intermediary does not have the keys. Keys live only in the client and the origin application. | [D], AES-GCM [SP 800-38D] |
| Body integrity and authenticity end to end (application to application) | Same condition. An intermediary that modifies the ciphertext, nonce or AAD causes a generic 400. | [D], tests `test_aead_rejects_plaintext_and_tampering`, `test_aead_rejects_foreign_key` |
| No cross-route or cross-direction reuse | The AAD binds version, direction, method, route and key ID, and each direction has its own key | [D], `test_route_bound_in_aad` (Phase 4) |
| Logged bodies are ciphertext | Middleware outside the application logs the body. The application itself must not log plaintext, and it does not (tests `test_no_secrets_in_logs`, `test_no_prompt_or_completion_in_logs`). | [D] |
| Payload still protected if one hop's TLS is misconfigured (verification off, downgrade) | Keys not exposed | [D] |

## 3. What it does not protect

- **The endpoints.** A compromised client or origin holds both the keys and the plaintext **[D]**.
- **Metadata.** Sizes (AES-GCM preserves length, plus a fixed overhead and Base64 expansion of about 33–36% **[M]**), timing, route, key ID, and in this implementation the `Server-Timing` header **[D]**.
  - In inference, timing reveals the response length. Generation time is linear in the number of tokens, at about 21 ms per token on the test system (H5 full run, reports/results-inference.md §2.4) **[M]**.
- **Replay on the same route.** A captured envelope can be resent and is accepted. The study has no timestamp, sequence number or nonce cache **[D]**.
- **Availability.** Decryption adds per-request CPU (18–53% more server CPU per request in Experiment A **[M]**), and unauthenticated garbage still costs a parse and a tag check **[D]**.
- **The model hop (Phase 4).** The API decrypts and forwards the prompt to Ollama in plaintext over local HTTP. The model must see plaintext **[D]**. Protecting a prompt up to the model would require the envelope to terminate inside the inference server, or in an attested trusted execution environment as in Apple's Private Cloud Compute design. There, load balancers and gateways "do not have the keys required to decrypt the user's request" [Apple PCC 2024] **[S]**.

## 4. Key management and nonces: the real costs

- **Key distribution is the main open problem.** The study uses two static symmetric keys, mounted as Docker secrets **[D]**. A real deployment needs:
  - **per-client keys:** a shared symmetric key across clients would turn any client compromise into a global one **[J]**;
  - **rotation**, a key-ID scheme and revocation;
  - usually **public-key encapsulation**, so that clients hold no long-term secret. HPKE [RFC 9180] is the standard construction **[S]**, and Oblivious HTTP [RFC 9458] uses it to encapsulate HTTP messages to a gateway **[S]**.
- **Nonces.** The implementation uses random 96-bit nonces and a per-key ceiling of 2³⁰ messages, far below the 2³² invocation bound for random nonces [SP 800-38D §8.3] **[D, S]**.
  - Nonce reuse under GCM is catastrophic for authenticity: it permits forgeries, and nonce reuse has been observed on real HTTPS servers [Böck et al. 2016] **[S]**.
  - Stateless horizontally scaled servers sharing one key must therefore either use random nonces with a strict per-key budget, or derive per-session keys **[J]**.
- **Failure modes introduced by the layer [J]:**
  - key mismatch after a partial rotation, which causes total outage of the encrypted route;
  - generic errors that are hard to debug;
  - gateways and web application firewalls can no longer inspect bodies, which is a loss of a security control as well as a gain;
  - observability tools see only ciphertext;
  - client libraries must implement the envelope correctly, which spreads cryptographic code across more codebases.

## 5. When the layer is justified: deployment assumptions

| Situation | Assessment |
|---|---|
| TLS terminates at infrastructure the data owner does not control or trust: third-party CDN or gateway, shared mesh, an outsourced logging pipeline, a multi-tenant queue | **Justified** if keys stay with the endpoints. This is the motivating model (threat-model.md §2). |
| Regulated payloads that pass through components that store bodies (integration engines, queues) | **Justified.** It also gives data-at-rest protection in those stores. |
| Prompts and completions with confidential content, sent through an API gateway or an observability stack operated by other teams | **Justified only if the envelope terminates at, or inside, the inference service.** Terminating at an API tier in front of the model (as in this lab) still exposes plaintext on the model hop. |
| Single-hop TLS from client to application, with no intermediary, or a terminator fully inside the same trust domain | **Little added confidentiality.** The cost (Experiment A) and the operational complexity are not offset. |
| The goal is integrity only, and intermediaries may legitimately read bodies | **A signature or MAC scheme may fit better than encryption.** HTTP Message Signatures [RFC 9421] lets intermediaries read the body while the recipient can detect tampering **[S]**. |
| Large or streamed bodies | A whole-message JSON/Base64 envelope is a poor fit. Record-based schemes such as `aes128gcm` [RFC 8188] permit incremental processing **[S]**. |

## 6. Distinguishing the two kinds of claim (for the paper)

- **Measured (performance):** the latency, throughput, CPU, memory and wire-size effects in reports/results.md, plus H5 when it is complete.
- **Not measured (security):** resistance to any particular adversary. These properties follow from AES-GCM's security as an AEAD [SP 800-38D] and from the implementation's tests, under the assumptions of threat-model.md. A sentence such as "the envelope protects bodies from a compromised load balancer" is acceptable only with the condition "if the load balancer does not hold the keys."
