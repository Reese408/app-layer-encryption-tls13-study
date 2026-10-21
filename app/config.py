"""Service configuration from environment variables (no secrets in the environment)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from app.workload import DEFAULT_ROUNDS

Mode = Literal["off", "aesgcm"]


@dataclass(frozen=True)
class Settings:
    mode: Mode
    key_file: str
    rounds: int
    max_messages_per_key: int
    ollama_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "qwen3:8b"

    @classmethod
    def from_env(cls) -> Settings:
        mode = os.environ.get("ENCRYPTION_MODE", "off")
        if mode not in ("off", "aesgcm"):
            raise ValueError("ENCRYPTION_MODE must be 'off' or 'aesgcm'")
        return cls(
            mode=mode,  # type: ignore[arg-type]
            key_file=os.environ.get("AEAD_KEY_FILE", "/run/secrets/aead_keys"),
            rounds=int(os.environ.get("WORKLOAD_ROUNDS", DEFAULT_ROUNDS)),
            max_messages_per_key=int(os.environ.get("AEAD_MAX_MESSAGES_PER_KEY", 2**30)),
            ollama_url=os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "qwen3:8b"),
        )
