"""Write the fixed prompt set for Experiment C (H5) to k6/fixtures/prompts.json.

Four prompts of about 1 KiB each: a technical context passage followed by an instruction
to write at length, so that generation runs to the token limit (done_reason=length) at both
response lengths. The text is fixed in this file, so the output is deterministic; its
SHA-256 is recorded in every run manifest.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "k6" / "fixtures" / "prompts.json"

INSTRUCTION = (
    " Write a long, detailed technical explanation of at least 1,500 words for a graduate "
    "security course. Use several paragraphs, cover design rationale, failure modes and "
    "practical deployment advice, and do not summarise or stop early."
)

CONTEXTS = [
    (
        "Context: An organisation runs a public HTTPS API behind a content delivery network, "
        "a regional load balancer and a service mesh. TLS 1.3 is terminated at the CDN edge "
        "and re-established between each hop, so request bodies exist in plaintext inside "
        "each intermediary. The security team is considering an additional AES-256-GCM "
        "envelope around every request and response body, with keys held only by the client "
        "application and the origin service. Engineers worry about key distribution, nonce "
        "management, replay of captured envelopes, larger payloads due to Base64 encoding, "
        "and the loss of request inspection by web application firewalls. Task: explain "
        "what such an envelope protects that hop-by-hop TLS does not, and what it cannot "
        "protect."
    ),
    (
        "Context: A hospital network exchanges laboratory results between an electronic "
        "health record system and several analysis services. Messages pass through an "
        "integration engine that logs every payload for troubleshooting, and through a "
        "message queue operated by a cloud provider. Regulations require confidentiality "
        "and integrity of patient data at rest and in transit, and auditors have asked "
        "whether transport encryption alone is sufficient when intermediaries can read and "
        "store message bodies. The architects propose authenticated encryption of message "
        "bodies with per-direction keys and associated data that binds the message type, "
        "the sender and a version number. Task: explain how authenticated encryption with "
        "associated data works and how it should be applied in this architecture."
    ),
    (
        "Context: A company serves a large language model to business customers through an "
        "API gateway that performs authentication, rate limiting and request logging. "
        "Prompts often contain confidential documents, and completions may contain "
        "summaries of those documents. The inference servers run on GPUs in a separate "
        "cluster, and the gateway, the logging pipeline and the observability platform are "
        "operated by different teams. A proposal would encrypt prompts end to end from the "
        "customer application to the inference service and decrypt them only inside the "
        "model server, perhaps within a trusted execution environment. Task: explain the "
        "threat model, the benefits and the operational costs of this proposal, including "
        "its effect on latency, streaming and debugging."
    ),
    (
        "Context: A software team must choose nonces for AES-GCM in a distributed system "
        "with many stateless application servers that share one long-lived key. Some "
        "engineers propose random 96-bit nonces, others propose counters coordinated "
        "through a database, and others propose deriving a fresh key per session with a "
        "key derivation function. The NIST recommendation for GCM limits the number of "
        "invocations with random nonces under one key, and nonce reuse destroys both "
        "confidentiality and authenticity for the affected messages. The team also needs "
        "key rotation without downtime and a clear way to detect replayed messages. Task: "
        "explain the cryptographic reasons behind these constraints and recommend a design."
    ),
]


def prompts() -> list[dict[str, str]]:
    return [{"id": f"p{i + 1}", "prompt": c + INSTRUCTION} for i, c in enumerate(CONTEXTS)]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    items = prompts()
    OUT.write_text(json.dumps({"items": items}, indent=1) + "\n")
    sizes = [len(it["prompt"].encode()) for it in items]
    print(f"{len(items)} prompts -> {OUT} (bytes: {sizes})")


if __name__ == "__main__":
    main()
