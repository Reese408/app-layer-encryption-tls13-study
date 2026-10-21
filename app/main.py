"""FastAPI service. One code path for both modes; only the envelope steps differ.

Stage timings are reported in a ``Server-Timing`` header (milliseconds):
``dec`` (envelope open), ``work`` (deterministic workload), ``enc`` (envelope seal),
``app`` (total handler time). In ``off`` mode ``dec`` and ``enc`` are reported as 0.

``/v1/infer`` (Phase 4) follows the same envelope steps around a call to the local Ollama
server and reports ``dec``, ``infer`` (wall time of that call), ``enc`` and ``app``.
"""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter_ns

from fastapi import FastAPI, Request, Response

from app.config import Settings
from app.crypto_envelope import EnvelopeCipher, InvalidEnvelope, KeySet, KeyUsageExceeded
from app.inference import InferenceUnavailable, InvalidInferRequest, Ollama, parse_request
from app.workload import InvalidPayload, canonical_json, process

log = logging.getLogger("aead_study")

PROCESS_PATH = "/v1/process"
INFER_PATH = "/v1/infer"
_JSON = "application/json"


def _ms(ns: int) -> str:
    return f"{ns / 1e6:.4f}"


def create_app(
    settings: Settings | None = None, keys: KeySet | None = None, ollama: Ollama | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    ollama = ollama or Ollama(settings.ollama_url, settings.ollama_model)
    cipher: EnvelopeCipher | None = None
    if settings.mode == "aesgcm":
        keys = keys or KeySet.load(settings.key_file)
        cipher = EnvelopeCipher(keys, "server", settings.max_messages_per_key)
    # Metadata only: never keys, plaintext, or ciphertext.
    log.info(
        "starting mode=%s kid=%s rounds=%d",
        settings.mode,
        cipher.kid if cipher else "-",
        settings.rounds,
    )

    app = FastAPI(title="AEAD over TLS 1.3 study", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "mode": settings.mode,
            "kid": cipher.kid if cipher else None,
            "rounds": settings.rounds,
        }

    @app.post(PROCESS_PATH)
    async def process_endpoint(request: Request) -> Response:
        t0 = perf_counter_ns()
        body = await request.body()
        t1 = perf_counter_ns()
        try:
            plaintext = cipher.open(body, "POST", PROCESS_PATH) if cipher else body
        except InvalidEnvelope:
            return Response(b'{"error":"invalid_envelope"}', status_code=400, media_type=_JSON)
        t2 = perf_counter_ns()
        try:
            out = process(plaintext, settings.rounds)
        except InvalidPayload:
            return Response(b'{"error":"invalid_payload"}', status_code=422, media_type=_JSON)
        t3 = perf_counter_ns()
        try:
            out = cipher.seal(out, "POST", PROCESS_PATH) if cipher else out
        except KeyUsageExceeded:
            log.error("key usage ceiling reached kid=%s", cipher.kid if cipher else "-")
            return Response(b'{"error":"key_exhausted"}', status_code=503, media_type=_JSON)
        t4 = perf_counter_ns()
        timing = (
            f"dec;dur={_ms(t2 - t1) if cipher else '0'}, work;dur={_ms(t3 - t2)}, "
            f"enc;dur={_ms(t4 - t3) if cipher else '0'}, app;dur={_ms(t4 - t0)}"
        )
        return Response(out, media_type=_JSON, headers={"Server-Timing": timing})

    @app.post(INFER_PATH)
    async def infer_endpoint(request: Request) -> Response:
        t0 = perf_counter_ns()
        body = await request.body()
        t1 = perf_counter_ns()
        try:
            plaintext = cipher.open(body, "POST", INFER_PATH) if cipher else body
        except InvalidEnvelope:
            return Response(b'{"error":"invalid_envelope"}', status_code=400, media_type=_JSON)
        t2 = perf_counter_ns()
        try:
            rid, prompt, max_tokens = parse_request(plaintext)
        except InvalidInferRequest:
            return Response(b'{"error":"invalid_payload"}', status_code=422, media_type=_JSON)
        ti0 = perf_counter_ns()
        try:
            result = await asyncio.to_thread(ollama.generate, prompt, max_tokens)
        except InferenceUnavailable:
            log.error("inference upstream unavailable")  # metadata only
            return Response(b'{"error":"upstream"}', status_code=502, media_type=_JSON)
        ti1 = perf_counter_ns()
        out = canonical_json({"id": rid, **result})
        t3 = perf_counter_ns()
        try:
            out = cipher.seal(out, "POST", INFER_PATH) if cipher else out
        except KeyUsageExceeded:
            log.error("key usage ceiling reached kid=%s", cipher.kid if cipher else "-")
            return Response(b'{"error":"key_exhausted"}', status_code=503, media_type=_JSON)
        t4 = perf_counter_ns()
        timing = (
            f"dec;dur={_ms(t2 - t1) if cipher else '0'}, infer;dur={_ms(ti1 - ti0)}, "
            f"enc;dur={_ms(t4 - t3) if cipher else '0'}, app;dur={_ms(t4 - t0)}"
        )
        return Response(out, media_type=_JSON, headers={"Server-Timing": timing})

    return app
