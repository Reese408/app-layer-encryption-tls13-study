"""Generate directional AES-256-GCM keys into secrets/aead_keys.json (gitignored).

Keys are produced by ``AESGCM.generate_key`` (OS CSPRNG) and are never printed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.crypto_envelope import KeySet  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "secrets" / "aead_keys.json"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    if OUT.exists() and not args.force:
        print(f"{OUT.relative_to(Path.cwd())} already present (use --force to rotate)")
        return
    OUT.parent.mkdir(mode=0o700, exist_ok=True)
    kid = "k" + dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    keys = KeySet.generate(kid)
    fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(keys.dump())
    print(f"wrote key set kid={kid} to {OUT.name} (key material not displayed)")


if __name__ == "__main__":
    main()
