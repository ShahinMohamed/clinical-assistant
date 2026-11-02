"""ASGI entrypoint for Vercel; reuse the local workbench's research pipeline."""

import json
import os
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from web.security import ResearchLimit, consume_research_budget, redact_secrets
from web.server import ASSETS, RUN_LOCK, STATIC, configuration, execute_request, validate_request


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def json_response(payload, status=200):
    return Response(
        json.dumps(redact_secrets(payload), ensure_ascii=False, default=str),
        status_code=status, media_type="application/json",
    )


@app.middleware("http")
async def response_headers(request, call_next):
    response = await call_next(request)
    response.headers.update({
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    })
    if os.getenv("VERCEL") == "1":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


@app.get("/api/status")
def status():
    state = configuration()
    if os.getenv("VERCEL") == "1":
        state.pop("missing", None)
    return json_response(state)


@app.post("/api/ask")
async def ask(request: Request):
    origin = request.headers.get("origin")
    if (origin and (
        urlsplit(origin).netloc != request.headers.get("host")
        or urlsplit(origin).scheme not in ("http", "https")
    )) or request.headers.get("sec-fetch-site") == "cross-site":
        return json_response({"message": "Run questions from this application."}, 403)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        return json_response({"message": "Send application/json."}, 415)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16384:
            return json_response({"message": "Invalid request size."}, 400)
    try:
        payload = json.loads(body)
        validate_request(payload)
    except (ValueError, UnicodeError) as error:
        return json_response({"message": str(error)}, 400)
    if not RUN_LOCK.acquire(blocking=False):
        return json_response({"message": "A pipeline is already running. Wait for it to finish."}, 409)

    def run():
        try:
            consume_research_budget(request.headers)
        except ResearchLimit as error:
            return json_response({"message": str(error)}, 429)
        except Exception:
            return json_response({"message": "Research is temporarily unavailable. Please try again later."}, 503)
        try:
            return json_response(execute_request(payload))
        except ValueError:
            return json_response({"message": "Research is temporarily unavailable."}, 503)

    try:
        return await run_in_threadpool(run)
    finally:
        RUN_LOCK.release()


@app.get("/{path:path}")
def asset(path: str):
    entry = ASSETS.get("/" + path)
    if entry is None:
        return json_response({"message": "Page not found."}, 404)
    filename, content_type = entry
    return Response((STATIC / filename).read_bytes(), media_type=content_type)
