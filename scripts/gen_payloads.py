"""Generate fixed-seed request payloads and their expected responses for k6.

Each fixture's request body is exactly ``size`` bytes of canonical JSON. The expected
response is computed with the same workload function the server uses, so k6 can check
byte-exact output integrity for every request.
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.workload import DEFAULT_ROUNDS, canonical_json, process  # noqa: E402

SIZES = {"1k": 1024, "16k": 16 * 1024, "256k": 256 * 1024}
ALPHABET = string.ascii_letters + string.digits


def make_request(rng: random.Random, idx: int, size: int) -> bytes:
    rid = f"req-{idx:04d}"
    overhead = len(canonical_json({"data": "", "id": rid}))
    data = "".join(rng.choices(ALPHABET, k=size - overhead))  # noqa: S311 - not security relevant
    body = canonical_json({"data": data, "id": rid})
    assert len(body) == size
    return body


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=20261020)
    p.add_argument("--pool", type=int, default=4, help="distinct payloads per size")
    p.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    p.add_argument("--out", type=Path, default=ROOT / "k6" / "fixtures")
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for label, size in SIZES.items():
        rng = random.Random(f"{args.seed}-{label}")  # noqa: S311
        items = []
        for i in range(args.pool):
            req = make_request(rng, i, size)
            resp = process(req, args.rounds)
            items.append(
                {
                    "req": req.decode(),
                    "req_b64": base64.b64encode(req).decode(),
                    "resp": resp.decode(),
                    "resp_b64": base64.b64encode(resp).decode(),
                }
            )
        meta = {"label": label, "size": size, "seed": args.seed, "rounds": args.rounds}
        (args.out / f"payloads-{label}.json").write_text(json.dumps({"meta": meta, "items": items}))
        print(f"payloads-{label}.json: {args.pool} x {size} B (rounds={args.rounds})")


if __name__ == "__main__":
    main()
