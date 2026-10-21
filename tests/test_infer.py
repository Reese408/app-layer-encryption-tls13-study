"""/v1/infer (Phase 4): plain/aead equivalence, validation and logging, with a fake Ollama."""

from __future__ import annotations

import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.crypto_envelope import EnvelopeCipher, KeySet
from app.inference import OPTIONS, InferenceUnavailable, Ollama
from app.main import create_app
from app.workload import canonical_json

PATH = "/v1/infer"
TIMING_RE = re.compile(
    r"^dec;dur=([\d.]+), infer;dur=([\d.]+), enc;dur=([\d.]+), app;dur=([\d.]+)$"
)
COMPLETION = "CANARY-COMPLETION-91c2 deterministic text"
PROMPT = "CANARY-PROMPT-4d1e explain AES-GCM"


class FakeOllama(Ollama):
    def __init__(self) -> None:
        super().__init__("http://unused", "qwen3:8b")
        object.__setattr__(self, "calls", [])

    def generate(self, prompt: str, max_tokens: int) -> dict:
        self.calls.append((prompt, max_tokens))  # type: ignore[attr-defined]
        meta = {"done_reason": "length", "eval_count": max_tokens, "model": self.model}
        return {"response": COMPLETION, "meta": meta}


def _settings(mode: str) -> Settings:
    return Settings(mode=mode, key_file="unused", rounds=16, max_messages_per_key=2**30)  # type: ignore[arg-type]


@pytest.fixture
def fake() -> FakeOllama:
    return FakeOllama()


@pytest.fixture
def clients(keys: KeySet, fake: FakeOllama) -> tuple[TestClient, TestClient]:
    return (
        TestClient(create_app(_settings("off"), ollama=fake)),
        TestClient(create_app(_settings("aesgcm"), keys=keys, ollama=fake)),
    )


def _req(n: int = 32) -> bytes:
    return canonical_json({"id": "p1", "max_tokens": n, "prompt": PROMPT})


def test_equivalence(clients, client_cipher: EnvelopeCipher, fake: FakeOllama) -> None:
    plain, aead = clients
    p = plain.post(PATH, content=_req())
    a = aead.post(PATH, content=client_cipher.seal(_req(), "POST", PATH))
    assert p.status_code == a.status_code == 200
    assert client_cipher.open(a.content, "POST", PATH) == p.content
    assert COMPLETION.encode() not in a.content  # ciphertext on the wire
    assert fake.calls == [(PROMPT, 32), (PROMPT, 32)]  # identical upstream work
    doc = json.loads(p.content)
    assert doc["id"] == "p1" and doc["response"] == COMPLETION
    assert doc["meta"]["eval_count"] == 32


def test_server_timing(clients, client_cipher: EnvelopeCipher) -> None:
    plain, aead = clients
    p = TIMING_RE.match(plain.post(PATH, content=_req()).headers["server-timing"])
    a = TIMING_RE.match(
        aead.post(PATH, content=client_cipher.seal(_req(), "POST", PATH)).headers["server-timing"]
    )
    assert p and a
    assert float(p[1]) == 0 and float(p[3]) == 0
    assert float(a[1]) > 0 and float(a[3]) > 0
    assert float(a[4]) >= float(a[1]) + float(a[2]) + float(a[3])


def test_route_bound_in_aad(clients, client_cipher: EnvelopeCipher) -> None:
    """An envelope sealed for /v1/process is rejected on /v1/infer."""
    _, aead = clients
    r = aead.post(PATH, content=client_cipher.seal(_req(), "POST", "/v1/process"))
    assert r.status_code == 400 and r.json() == {"error": "invalid_envelope"}


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'{"id":"p1","prompt":"x"}',
        b'{"id":"p1","prompt":"","max_tokens":4}',
        b'{"id":"p1","prompt":"x","max_tokens":0}',
        b'{"id":"p1","prompt":"x","max_tokens":4096}',
        b'{"id":"p1","prompt":"x","max_tokens":true}',
    ],
)
def test_invalid_request_same_in_both_modes(clients, client_cipher, fake, body: bytes) -> None:
    plain, aead = clients
    assert plain.post(PATH, content=body).status_code == 422
    assert aead.post(PATH, content=client_cipher.seal(body, "POST", PATH)).status_code == 422
    assert fake.calls == []


def test_upstream_failure_is_generic(keys: KeySet, client_cipher: EnvelopeCipher) -> None:
    class Down(Ollama):
        def generate(self, prompt: str, max_tokens: int) -> dict:
            raise InferenceUnavailable

    c = TestClient(create_app(_settings("aesgcm"), keys=keys, ollama=Down("http://x", "m")))
    r = c.post(PATH, content=client_cipher.seal(_req(), "POST", PATH))
    assert r.status_code == 502 and r.json() == {"error": "upstream"}


def test_no_prompt_or_completion_in_logs(caplog, capsys, clients, client_cipher) -> None:
    caplog.set_level(logging.DEBUG)
    plain, aead = clients
    plain.post(PATH, content=_req())
    aead.post(PATH, content=client_cipher.seal(_req(), "POST", PATH))
    captured = caplog.text + capsys.readouterr().out
    assert PROMPT not in captured and COMPLETION not in captured


def test_ollama_client_request_and_parsing() -> None:
    """The real client sends the fixed options and extracts the metadata fields."""
    seen: dict = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            seen["path"] = self.path
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            out = json.dumps(
                {"model": "qwen3:8b", "response": "ok", "done_reason": "length", "eval_count": 8}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a) -> None:
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    try:
        r = Ollama(f"http://127.0.0.1:{srv.server_port}", "qwen3:8b").generate("hi", 8)
    finally:
        srv.server_close()
    assert seen["path"] == "/api/generate"
    b = seen["body"]
    assert b["stream"] is False and b["think"] is False and b["keep_alive"] == -1
    assert b["options"] == {**OPTIONS, "num_predict": 8}
    assert r["response"] == "ok" and r["meta"]["eval_count"] == 8
    assert r["meta"]["prompt_eval_count"] is None  # absent fields are explicit nulls


def test_ollama_unreachable() -> None:
    with pytest.raises(InferenceUnavailable):
        Ollama("http://127.0.0.1:9", "m", timeout_s=1).generate("x", 1)
