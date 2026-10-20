from __future__ import annotations

import datetime as dt
import ipaddress

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app.config import Settings
from app.crypto_envelope import EnvelopeCipher, KeySet
from app.main import create_app


@pytest.fixture
def keys() -> KeySet:
    # Generated per test; no committed key material.
    return KeySet.generate("ktest")


@pytest.fixture
def client_cipher(keys: KeySet) -> EnvelopeCipher:
    return EnvelopeCipher(keys, "client")


def _settings(mode: str, max_messages: int = 2**30) -> Settings:
    return Settings(mode=mode, key_file="unused", rounds=16, max_messages_per_key=max_messages)  # type: ignore[arg-type]


@pytest.fixture
def plain_client() -> TestClient:
    return TestClient(create_app(_settings("off")))


@pytest.fixture
def aead_client(keys: KeySet) -> TestClient:
    return TestClient(create_app(_settings("aesgcm"), keys=keys))


@pytest.fixture
def make_aead_client(keys: KeySet):
    def factory(max_messages: int) -> TestClient:
        return TestClient(create_app(_settings("aesgcm", max_messages), keys=keys))

    return factory


@pytest.fixture(scope="session")
def tls_cert(tmp_path_factory) -> tuple[str, str]:
    """Self-signed ECDSA P-256 cert for 127.0.0.1, generated at test time."""
    d = tmp_path_factory.mktemp("tls")
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = d / "server.pem", d / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)
