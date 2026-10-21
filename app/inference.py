"""Client for the local Ollama inference server (Phase 4, Experiment C).

Standard library only (no new runtime dependency). The call is blocking; the API runs it in
a worker thread so the event loop is not blocked. Generation options are fixed here and are
identical in both encryption modes (research-design.md §7.1). Prompts and completions are
never logged.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

# Fixed for every request, including warm-up. `num_ctx` must never vary: a different context
# size makes Ollama reload the model.
OPTIONS: dict[str, Any] = {
    "temperature": 0,
    "seed": 42,
    "top_k": 20,
    "top_p": 0.95,
    "repeat_penalty": 1,
    "num_ctx": 4096,
}
MAX_TOKENS_LIMIT = 1024
META_FIELDS = (
    "done_reason",
    "prompt_eval_count",
    "prompt_eval_duration",
    "eval_count",
    "eval_duration",
    "load_duration",
    "total_duration",
)


class InvalidInferRequest(Exception):  # noqa: N818
    pass


class InferenceUnavailable(Exception):  # noqa: N818
    pass


def parse_request(plaintext: bytes) -> tuple[str, str, int]:
    """Validate a plaintext /v1/infer body; return (id, prompt, max_tokens)."""
    try:
        doc = json.loads(plaintext)
    except ValueError:
        raise InvalidInferRequest from None
    if not isinstance(doc, dict):
        raise InvalidInferRequest
    rid, prompt, n = doc.get("id"), doc.get("prompt"), doc.get("max_tokens")
    if not isinstance(rid, str) or not isinstance(prompt, str) or not prompt:
        raise InvalidInferRequest
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_TOKENS_LIMIT:
        raise InvalidInferRequest
    return rid, prompt, n


@dataclass(frozen=True)
class Ollama:
    url: str
    model: str
    timeout_s: float = 120.0

    def request_body(self, prompt: str, max_tokens: int) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "keep_alive": -1,
            "options": {**OPTIONS, "num_predict": max_tokens},
        }

    def generate(self, prompt: str, max_tokens: int) -> dict[str, Any]:
        """Return {"response": str, "meta": {...}} from a non-streaming /api/generate call."""
        req = urllib.request.Request(  # noqa: S310 - fixed http(s) URL from configuration
            f"{self.url}/api/generate",
            data=json.dumps(self.request_body(prompt, max_tokens)).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310
                doc = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            raise InferenceUnavailable from None
        if not isinstance(doc.get("response"), str):
            raise InferenceUnavailable
        meta = {k: doc.get(k) for k in META_FIELDS}
        meta["model"] = doc.get("model")
        return {"response": doc["response"], "meta": meta}
