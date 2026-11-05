"""Launch Uvicorn with a TLS 1.3-only server context (single worker)."""

from __future__ import annotations

import argparse
import logging
import ssl

import uvicorn


class TLS13Config(uvicorn.Config):
    """Uvicorn's default SSL context allows TLS 1.2; pin the minimum to TLS 1.3."""

    def load(self) -> None:
        super().load()
        if self.ssl is not None:
            self.ssl.minimum_version = ssl.TLSVersion.TLSv1_3


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container-internal bind
    p.add_argument("--port", type=int, default=8443)
    p.add_argument("--certfile", default="/certs/server.pem")
    p.add_argument("--keyfile", default="/certs/server.key")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = TLS13Config(
        "app.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=1,
        ssl_certfile=args.certfile,
        ssl_keyfile=args.keyfile,
        access_log=False,  # disabled in both modes: equal cost, and no request metadata leakage
        log_level="info",
        http="httptools",
        loop="uvloop",
    )
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
