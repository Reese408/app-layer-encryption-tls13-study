"""Record validation evidence: TLS negotiation per service and resource-sampler accuracy.

Output: results/raw/<UTC timestamp>-validation/
  tls_<service>_<version>.txt   raw `openssl s_client` transcripts (certificates omitted)
  tls_summary.json              negotiated protocol/cipher/verify result per attempt
  sampler_validation.{csv,json} see scripts/validate_sampler.py
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = {"api-plain": 8443, "api-aead": 8444}


def s_client(port: int, flag: str) -> str:
    p = subprocess.run(
        [
            "openssl",
            "s_client",
            "-connect",
            f"localhost:{port}",
            flag,
            "-CAfile",
            str(ROOT / "certs" / "ca.pem"),
            "-servername",
            "localhost",
            "-brief",
        ],
        input="",
        capture_output=True,
        text=True,
        timeout=15,
    )
    return p.stdout + p.stderr


def main() -> None:
    out = ROOT / "results" / "raw" / f"{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}-validation"
    out.mkdir(parents=True)
    summary, ok = [], True
    for svc, port in SERVICES.items():
        for flag, expect_ok in (("-tls1_3", True), ("-tls1_2", False)):
            text = s_client(port, flag)
            (out / f"tls_{svc}_{flag.strip('-')}.txt").write_text(text)
            proto = re.search(r"Protocol version: (\S+)", text)
            cipher = re.search(r"Ciphersuite: (\S+)", text)
            verified = "Verification: OK" in text
            negotiated = bool(proto)
            passed = negotiated == expect_ok and (
                not expect_ok or (verified and proto[1] == "TLSv1.3")
            )
            ok &= passed
            summary.append(
                {
                    "service": svc,
                    "client_offer": flag,
                    "negotiated": negotiated,
                    "protocol": proto[1] if proto else None,
                    "cipher": cipher[1] if cipher else None,
                    "cert_verified": verified,
                    "expected_success": expect_ok,
                    "pass": passed,
                }
            )
            print(
                f"{svc:9s} {flag}: negotiated={negotiated} "
                f"protocol={proto[1] if proto else '-'} cipher={cipher[1] if cipher else '-'} "
                f"verified={verified} pass={passed}"
            )
    (out / "tls_summary.json").write_text(json.dumps(summary, indent=2))
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_sampler.py"), "--out", str(out)]
    )
    ok &= r.returncode == 0
    print(f"validation evidence -> {out.relative_to(ROOT)}; overall pass={ok}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
