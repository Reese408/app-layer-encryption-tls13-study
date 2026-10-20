"""TLS 1.3 enforcement on the real Uvicorn launcher (subprocess, loopback only)."""

from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tls_cert):
    cert, key = tls_cert
    port = _free_port()
    env = {**os.environ, "ENCRYPTION_MODE": "off"}
    proc = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "app.server",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--certfile",
            cert,
            "--keyfile",
            key,
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("server did not start: " + proc.stdout.read().decode())
    yield port, cert
    proc.terminate()
    proc.wait(timeout=10)


def _ctx(cert: str, max_version: ssl.TLSVersion | None = None) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=cert)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if max_version:
        ctx.maximum_version = max_version
    return ctx


def test_negotiates_tls13(server) -> None:
    port, cert = server
    with socket.create_connection(("127.0.0.1", port)) as raw:
        with _ctx(cert).wrap_socket(raw, server_hostname="localhost") as tls:
            assert tls.version() == "TLSv1.3"
            assert tls.cipher()[0].startswith("TLS_")  # TLS 1.3 suite names
            tls.sendall(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            resp = b""
            while chunk := tls.recv(4096):
                resp += chunk
    assert resp.startswith(b"HTTP/1.1 200") and b'"mode":"off"' in resp


def test_rejects_tls12(server) -> None:
    port, cert = server
    with socket.create_connection(("127.0.0.1", port)) as raw:
        with pytest.raises(ssl.SSLError):
            with _ctx(cert, ssl.TLSVersion.TLSv1_2).wrap_socket(raw, server_hostname="localhost"):
                pass


def test_client_verifies_certificate(server, tmp_path) -> None:
    """Sanity check that verification is real: an unrelated CA bundle must fail."""
    port, _ = server
    with socket.create_connection(("127.0.0.1", port)) as raw:
        ctx = ssl.create_default_context()  # system roots only; our cert is self-signed
        with pytest.raises(ssl.SSLCertVerificationError):
            with ctx.wrap_socket(raw, server_hostname="localhost"):
                pass
