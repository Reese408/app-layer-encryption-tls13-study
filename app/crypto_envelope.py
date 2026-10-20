"""AES-256-GCM payload envelope.

All cryptography is delegated to ``cryptography``'s ``AESGCM`` (OpenSSL backend).
This module only handles key loading, nonce generation, associated data (AAD)
construction, envelope (de)serialization, and the per-key usage ceiling.

Envelope (JSON):  {"v": 1, "kid": str, "n": b64(nonce[12]), "ct": b64(ciphertext || tag[16])}
AAD (UTF-8):      "v1|<direction>|<METHOD>|<path>|<kid>"
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENVELOPE_VERSION = 1
KEY_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16
# NIST SP 800-38D §8.3 caps random-nonce GCM at 2**32 invocations per key.
# The default ceiling is deliberately far below that bound.
DEFAULT_MAX_MESSAGES_PER_KEY = 2**30

Direction = Literal["c2s", "s2c"]


class InvalidEnvelope(Exception):  # noqa: N818 - public name used in API error mapping
    """Raised for any malformed, unauthenticated, or mismatched envelope.

    The message is intentionally generic so callers cannot distinguish tag, nonce,
    AAD, or parse failures.
    """

    def __init__(self) -> None:
        super().__init__("invalid_envelope")


class KeyUsageExceeded(Exception):  # noqa: N818
    """Raised when a key reaches its configured encryption ceiling (fail closed)."""


@dataclass(frozen=True, repr=False)
class KeySet:
    """Directional AES-256 keys. ``repr`` is suppressed so keys never reach logs."""

    kid: str
    c2s: bytes
    s2c: bytes

    def __post_init__(self) -> None:
        for name in ("c2s", "s2c"):
            if len(getattr(self, name)) != KEY_BYTES:
                raise ValueError(f"{name} key must be {KEY_BYTES} bytes")
        if self.c2s == self.s2c:
            raise ValueError("directional keys must differ")
        if not self.kid or "|" in self.kid:
            raise ValueError("kid must be non-empty and must not contain '|'")

    def __repr__(self) -> str:
        return f"KeySet(kid={self.kid!r}, c2s=<redacted>, s2c=<redacted>)"

    @classmethod
    def generate(cls, kid: str) -> KeySet:
        return cls(kid=kid, c2s=AESGCM.generate_key(256), s2c=AESGCM.generate_key(256))

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> KeySet:
        doc = json.loads(Path(path).read_text())
        return cls(
            kid=doc["kid"],
            c2s=base64.b64decode(doc["c2s"], validate=True),
            s2c=base64.b64decode(doc["s2c"], validate=True),
        )

    def dump(self) -> str:
        return json.dumps(
            {
                "kid": self.kid,
                "alg": "AES-256-GCM",
                "c2s": base64.b64encode(self.c2s).decode(),
                "s2c": base64.b64encode(self.s2c).decode(),
            },
            indent=2,
        )


def build_aad(direction: Direction, method: str, path: str, kid: str) -> bytes:
    return f"v{ENVELOPE_VERSION}|{direction}|{method.upper()}|{path}|{kid}".encode()


@dataclass
class _Sealer:
    aead: AESGCM
    limit: int
    count: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reserve(self) -> None:
        with self.lock:
            if self.count >= self.limit:
                raise KeyUsageExceeded("encryption ceiling reached for key; rotate keys")
            self.count += 1


class EnvelopeCipher:
    """Seals and opens envelopes for one party (``role``) of the protocol.

    The server encrypts with ``s2c`` and decrypts with ``c2s``; the client does the opposite.
    """

    def __init__(
        self,
        keys: KeySet,
        role: Literal["server", "client"] = "server",
        max_messages_per_key: int = DEFAULT_MAX_MESSAGES_PER_KEY,
    ) -> None:
        if max_messages_per_key < 1:
            raise ValueError("max_messages_per_key must be >= 1")
        self.kid = keys.kid
        self.role = role
        self._seal_dir: Direction = "s2c" if role == "server" else "c2s"
        self._open_dir: Direction = "c2s" if role == "server" else "s2c"
        seal_key = keys.s2c if role == "server" else keys.c2s
        open_key = keys.c2s if role == "server" else keys.s2c
        self._sealer = _Sealer(AESGCM(seal_key), max_messages_per_key)
        self._opener = AESGCM(open_key)

    @property
    def messages_sealed(self) -> int:
        return self._sealer.count

    def seal(self, plaintext: bytes, method: str, path: str) -> bytes:
        self._sealer.reserve()
        nonce = os.urandom(NONCE_BYTES)
        aad = build_aad(self._seal_dir, method, path, self.kid)
        ct = self._sealer.aead.encrypt(nonce, plaintext, aad)
        return json.dumps(
            {
                "v": ENVELOPE_VERSION,
                "kid": self.kid,
                "n": base64.b64encode(nonce).decode(),
                "ct": base64.b64encode(ct).decode(),
            },
            separators=(",", ":"),
        ).encode()

    def open(self, envelope: bytes, method: str, path: str) -> bytes:
        try:
            doc = json.loads(envelope)
            if not isinstance(doc, dict) or doc.get("v") != ENVELOPE_VERSION:
                raise InvalidEnvelope
            if doc.get("kid") != self.kid:
                raise InvalidEnvelope
            nonce = base64.b64decode(doc["n"], validate=True)
            ct = base64.b64decode(doc["ct"], validate=True)
            if len(nonce) != NONCE_BYTES or len(ct) < TAG_BYTES:
                raise InvalidEnvelope
            aad = build_aad(self._open_dir, method, path, self.kid)
            return self._opener.decrypt(nonce, ct, aad)
        except InvalidEnvelope:
            raise
        except (InvalidTag, ValueError, KeyError, TypeError, binascii.Error):
            # json.JSONDecodeError and UnicodeDecodeError are ValueError subclasses.
            raise InvalidEnvelope from None
