"""Functional behavior and plain/aead equivalence through the HTTP layer."""

from __future__ import annotations

import json
import logging
import re

import pytest

from app.crypto_envelope import EnvelopeCipher, KeySet
from app.workload import canonical_json, process

PATH = "/v1/process"
TIMING_RE = re.compile(r"^dec;dur=([\d.]+), work;dur=([\d.]+), enc;dur=([\d.]+), app;dur=([\d.]+)$")


def _payload(size: int, marker: str = "") -> bytes:
    base = canonical_json({"data": "", "id": "r1"})
    data = (marker + "abc123" * size)[: size - len(base)]
    return canonical_json({"data": data, "id": "r1"})


def test_health(plain_client, aead_client, keys: KeySet) -> None:
    assert plain_client.get("/healthz").json() == {
        "status": "ok",
        "mode": "off",
        "kid": None,
        "rounds": 16,
    }
    h = aead_client.get("/healthz").json()
    assert h["mode"] == "aesgcm" and h["kid"] == keys.kid
    assert set(h) == {"status", "mode", "kid", "rounds"}  # no key material exposed


@pytest.mark.parametrize("size", [1024, 16 * 1024, 256 * 1024])
def test_functional_equivalence(plain_client, aead_client, client_cipher, size: int) -> None:
    """Decrypted aead response is byte-identical to the plain response and the reference."""
    req = _payload(size)
    plain = plain_client.post(PATH, content=req)
    env = client_cipher.seal(req, "POST", PATH)
    aead = aead_client.post(PATH, content=env)
    assert plain.status_code == aead.status_code == 200
    decrypted = client_cipher.open(aead.content, "POST", PATH)
    assert decrypted == plain.content == process(req, 16)
    assert req not in aead.content  # response body on the wire is ciphertext
    out = json.loads(decrypted)
    assert out["size"] == size and len(out["digest"]) == 64


def test_server_timing_headers(plain_client, aead_client, client_cipher) -> None:
    req = _payload(1024)
    p = TIMING_RE.match(plain_client.post(PATH, content=req).headers["server-timing"])
    a = TIMING_RE.match(
        aead_client.post(PATH, content=client_cipher.seal(req, "POST", PATH)).headers[
            "server-timing"
        ]
    )
    assert p and a
    assert float(p[1]) == 0 and float(p[3]) == 0
    assert float(a[1]) > 0 and float(a[3]) > 0
    assert float(a[4]) >= float(a[1]) + float(a[2]) + float(a[3])


def test_aead_rejects_plaintext_and_tampering(aead_client, client_cipher) -> None:
    req = _payload(1024)
    r = aead_client.post(PATH, content=req)
    assert r.status_code == 400 and r.json() == {"error": "invalid_envelope"}
    env = json.loads(client_cipher.seal(req, "POST", PATH))
    env["ct"] = ("A" if env["ct"][0] != "A" else "B") + env["ct"][1:]
    r = aead_client.post(PATH, content=json.dumps(env))
    assert r.status_code == 400 and r.json() == {"error": "invalid_envelope"}
    assert "server-timing" not in r.headers


def test_aead_rejects_foreign_key(aead_client, keys: KeySet) -> None:
    foreign = EnvelopeCipher(KeySet.generate(keys.kid), "client")
    r = aead_client.post(PATH, content=foreign.seal(_payload(1024), "POST", PATH))
    assert r.status_code == 400 and r.json() == {"error": "invalid_envelope"}


def test_invalid_payload_same_in_both_modes(plain_client, aead_client, client_cipher) -> None:
    bad = b'{"id": 5}'
    assert plain_client.post(PATH, content=bad).status_code == 422
    assert aead_client.post(PATH, content=client_cipher.seal(bad, "POST", PATH)).status_code == 422


def test_server_key_ceiling_returns_503(make_aead_client, client_cipher) -> None:
    c = make_aead_client(max_messages=2)
    req = _payload(1024)
    codes = [
        c.post(PATH, content=client_cipher.seal(req, "POST", PATH)).status_code for _ in range(3)
    ]
    assert codes == [200, 200, 503]


def test_no_secrets_in_logs(caplog, capsys, keys: KeySet, aead_client, client_cipher) -> None:
    marker = "CANARY-PLAINTEXT-7f3a"
    caplog.set_level(logging.DEBUG)
    req = _payload(1024, marker)
    env = client_cipher.seal(req, "POST", PATH)
    resp = aead_client.post(PATH, content=env)
    aead_client.post(PATH, content=b"garbage")
    captured = caplog.text + capsys.readouterr().out + capsys.readouterr().err
    forbidden = [
        marker,
        keys.c2s.hex(),
        keys.s2c.hex(),
        __import__("base64").b64encode(keys.c2s).decode(),
        __import__("base64").b64encode(keys.s2c).decode(),
        json.loads(env)["ct"][:32],
        json.loads(resp.content)["ct"][:32],
    ]
    for f in forbidden:
        assert f not in captured
