"""AES-256-GCM envelope correctness, nonce handling, and authentication failures."""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.crypto_envelope import (
    NONCE_BYTES,
    TAG_BYTES,
    EnvelopeCipher,
    InvalidEnvelope,
    KeySet,
    KeyUsageExceeded,
    build_aad,
)

PATH = "/v1/process"


def _pair(keys: KeySet) -> tuple[EnvelopeCipher, EnvelopeCipher]:
    return EnvelopeCipher(keys, "client"), EnvelopeCipher(keys, "server")


def _mutate(env: bytes, field: str, fn) -> bytes:
    doc = json.loads(env)
    raw = bytearray(base64.b64decode(doc[field]))
    fn(raw)
    doc[field] = base64.b64encode(bytes(raw)).decode()
    return json.dumps(doc).encode()


# --- Known-answer tests: confirm the library is used as AES-256-GCM -------------------------
# McGrew & Viega, "The Galois/Counter Mode of Operation", test cases 13 and 14 (256-bit key).
@pytest.mark.parametrize(
    ("pt", "expected"),
    [
        (b"", "530f8afbc74536b9a963b4f1c4cb738b"),
        (bytes(16), "cea7403d4d606b6e074ec5d3baf39d18d0d1c8a799996bf0265b98b5d48ab919"),
    ],
)
def test_aes256gcm_known_answer(pt: bytes, expected: str) -> None:
    assert AESGCM(bytes(32)).encrypt(bytes(12), pt, None).hex() == expected


# --- Round trip and format ------------------------------------------------------------------
@pytest.mark.parametrize("size", [0, 1, 1024, 16 * 1024, 256 * 1024])
def test_round_trip_and_format(keys: KeySet, size: int) -> None:
    client, server = _pair(keys)
    pt = bytes(range(256)) * (size // 256) + bytes(size % 256)
    env = client.seal(pt, "POST", PATH)
    doc = json.loads(env)
    assert set(doc) == {"v", "kid", "n", "ct"} and doc["v"] == 1 and doc["kid"] == keys.kid
    assert len(base64.b64decode(doc["n"])) == NONCE_BYTES
    ct = base64.b64decode(doc["ct"])
    assert len(ct) == size + TAG_BYTES
    if size >= 16:
        assert pt not in ct
    assert server.open(env, "POST", PATH) == pt


def test_both_directions(keys: KeySet) -> None:
    client, server = _pair(keys)
    assert client.open(server.seal(b"resp", "POST", PATH), "POST", PATH) == b"resp"


# --- Nonce handling -------------------------------------------------------------------------
def test_nonces_unique_and_ciphertexts_differ(keys: KeySet) -> None:
    client = EnvelopeCipher(keys, "client")
    n = 20_000
    envs = [json.loads(client.seal(b"same plaintext", "POST", PATH)) for _ in range(n)]
    assert len({e["n"] for e in envs}) == n
    assert len({e["ct"] for e in envs}) == n


def test_usage_ceiling_fails_closed(keys: KeySet) -> None:
    client = EnvelopeCipher(keys, "client", max_messages_per_key=3)
    for _ in range(3):
        client.seal(b"x", "POST", PATH)
    assert client.messages_sealed == 3
    with pytest.raises(KeyUsageExceeded):
        client.seal(b"x", "POST", PATH)
    assert client.messages_sealed == 3


# --- Authentication failures ----------------------------------------------------------------
def _flip_last(b: bytearray) -> None:
    b[-1] ^= 0x01


def _flip_first(b: bytearray) -> None:
    b[0] ^= 0x80


@pytest.mark.parametrize(
    "tamper",
    [
        lambda e: _mutate(e, "ct", _flip_first),  # ciphertext body
        lambda e: _mutate(e, "ct", _flip_last),  # authentication tag
        lambda e: _mutate(e, "n", _flip_first),  # nonce
        lambda e: _mutate(e, "ct", lambda b: b.__delitem__(slice(-1, None))),  # truncated tag
        lambda e: _mutate(e, "n", lambda b: b.extend(b"\0")),  # wrong nonce length
    ],
    ids=["ciphertext", "tag", "nonce", "truncated", "nonce-length"],
)
def test_tampering_rejected(keys: KeySet, tamper) -> None:
    client, server = _pair(keys)
    env = client.seal(b"secret payload" * 8, "POST", PATH)
    with pytest.raises(InvalidEnvelope) as exc:
        server.open(tamper(env), "POST", PATH)
    assert str(exc.value) == "invalid_envelope"


@pytest.mark.parametrize(
    ("method", "path"), [("PUT", PATH), ("POST", "/v1/other"), ("POST", PATH + "/")]
)
def test_aad_binds_method_and_route(keys: KeySet, method: str, path: str) -> None:
    client, server = _pair(keys)
    env = client.seal(b"payload", "POST", PATH)
    with pytest.raises(InvalidEnvelope):
        server.open(env, method, path)


def test_reflection_rejected(keys: KeySet) -> None:
    """A server response cannot be replayed to the server as a request (directional keys/AAD)."""
    server = EnvelopeCipher(keys, "server")
    with pytest.raises(InvalidEnvelope):
        server.open(server.seal(b"payload", "POST", PATH), "POST", PATH)


def test_wrong_key_rejected(keys: KeySet) -> None:
    other = KeySet.generate(keys.kid)
    env = EnvelopeCipher(other, "client").seal(b"payload", "POST", PATH)
    with pytest.raises(InvalidEnvelope):
        EnvelopeCipher(keys, "server").open(env, "POST", PATH)


def test_kid_mismatch_rejected(keys: KeySet) -> None:
    client, server = _pair(keys)
    doc = json.loads(client.seal(b"payload", "POST", PATH))
    doc["kid"] = "kother"
    with pytest.raises(InvalidEnvelope):
        server.open(json.dumps(doc).encode(), "POST", PATH)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"[]",
        b'{"v":2,"kid":"ktest","n":"","ct":""}',
        b'{"v":1,"kid":"ktest"}',
        b'{"v":1,"kid":"ktest","n":"!!!","ct":"AAAA"}',
        b'{"v":1,"kid":"ktest","n":123,"ct":"AAAA"}',
        b"\xff\xfe",
    ],
)
def test_malformed_rejected_generically(keys: KeySet, body: bytes) -> None:
    with pytest.raises(InvalidEnvelope) as exc:
        EnvelopeCipher(keys, "server").open(body, "POST", PATH)
    assert str(exc.value) == "invalid_envelope"


# --- Key handling ---------------------------------------------------------------------------
def test_keyset_validation() -> None:
    with pytest.raises(ValueError):
        KeySet("k", bytes(16), bytes(32))
    with pytest.raises(ValueError):
        KeySet("k", bytes(32), bytes(32))
    with pytest.raises(ValueError):
        KeySet("a|b", b"\1" * 32, b"\2" * 32)


def test_keyset_repr_redacts(keys: KeySet) -> None:
    r = repr(keys)
    assert "redacted" in r
    assert base64.b64encode(keys.c2s).decode() not in r and keys.c2s.hex() not in r


def test_keyset_dump_load_round_trip(tmp_path, keys: KeySet) -> None:
    p = tmp_path / "k.json"
    p.write_text(keys.dump())
    loaded = KeySet.load(p)
    assert (loaded.kid, loaded.c2s, loaded.s2c) == (keys.kid, keys.c2s, keys.s2c)


def test_aad_format() -> None:
    assert build_aad("c2s", "post", PATH, "k1") == b"v1|c2s|POST|/v1/process|k1"
