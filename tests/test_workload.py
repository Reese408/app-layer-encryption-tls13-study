from __future__ import annotations

import hashlib
import json

import pytest

from app.workload import InvalidPayload, canonical_json, process


def test_deterministic_and_canonicalizing() -> None:
    a = process(b'{"id":"x","data":"abc"}')
    b = process(b'{ "data": "abc", "id": "x" }')
    assert a == b == process(b'{"id":"x","data":"abc"}')


def test_digest_matches_reference_chain() -> None:
    canonical = canonical_json({"data": "abc", "id": "x"})
    d = hashlib.sha256(canonical).digest()
    for _ in range(3):
        d = hashlib.sha256(d + canonical).digest()
    out = json.loads(process(canonical, rounds=4))
    assert out == {"digest": d.hex(), "echo": "abc", "id": "x", "rounds": 4, "size": len(canonical)}


@pytest.mark.parametrize("body", [b"", b"[]", b'{"id":1,"data":"a"}', b'{"id":"a"}', b"\xff"])
def test_invalid(body: bytes) -> None:
    with pytest.raises(InvalidPayload):
        process(body)
