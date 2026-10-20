"""Deterministic application workload, identical in both encryption modes.

The workload canonicalizes the JSON request, computes an iterated SHA-256 chain over
the canonical bytes (cost proportional to payload size x rounds), and returns a
canonical JSON response that echoes the payload so the response size tracks the
request size.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

DEFAULT_ROUNDS = 16


class InvalidPayload(Exception):  # noqa: N818
    pass


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def process(plaintext: bytes, rounds: int = DEFAULT_ROUNDS) -> bytes:
    """Return the canonical response body for a canonical-or-not JSON request body."""
    try:
        doc = json.loads(plaintext)
    except ValueError:
        raise InvalidPayload from None
    if (
        not isinstance(doc, dict)
        or not isinstance(doc.get("id"), str)
        or not isinstance(doc.get("data"), str)
    ):
        raise InvalidPayload
    canonical = canonical_json(doc)
    digest = hashlib.sha256(canonical).digest()
    for _ in range(rounds - 1):
        digest = hashlib.sha256(digest + canonical).digest()
    return canonical_json(
        {
            "id": doc["id"],
            "rounds": rounds,
            "size": len(canonical),
            "digest": digest.hex(),
            "echo": doc["data"],
        }
    )
