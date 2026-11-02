"""Serve the workbench: python -m web.server [--port 8000]."""

import argparse
import json
import os
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore
from time import perf_counter
from urllib.parse import urlsplit

from assistant.tracing import redact_blocked_outputs
from safety.checks import check_input
from web.security import ResearchLimit, consume_research_budget, redact_secrets

if os.getenv("VERCEL") == "1":
    import certifi
    os.environ.setdefault("PGSSLROOTCERT", certifi.where())


ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
FEATURES = ("hyde", "rerank", "crag", "web")
REQUIRED_SETTINGS = ("DATABASE_URL", "GEMINI_API_KEY", "GEMINI_MODEL")
RUN_LOCK = BoundedSemaphore(1)
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}


def load_settings():
    if os.getenv("VERCEL") == "1":
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


def configuration():
    missing = [name for name in REQUIRED_SETTINGS if not os.getenv(name, "").strip()]
    return {
        "configured": not missing,
        "missing": missing,
        "research_only": True,
        "features": list(FEATURES),
    }


def validate_request(payload):
    if not isinstance(payload, dict):
        raise ValueError("Send a JSON object.")
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Enter a research question.")
    if len(question) > 2000:
        raise ValueError("Keep the question within 2,000 characters.")
    selected = payload.get("features", {})
    if not isinstance(selected, dict) or set(selected) - set(FEATURES):
        raise ValueError("Choose supported pipeline features.")
    if any(type(value) is not bool for value in selected.values()):
        raise ValueError("Feature selections must be true or false.")
    options = {name: selected.get(name, False) for name in FEATURES}
    if options["web"] and not options["crag"]:
        raise ValueError("Web fallback requires CRAG. Enable the evidence check first.")
    analysis_date = payload.get("analysis_date") or None
    if analysis_date is not None:
        if not isinstance(analysis_date, str):
            raise ValueError("Use YYYY-MM-DD for the analysis date.")
        try:
            analysis_date = date.fromisoformat(analysis_date).isoformat()
        except ValueError:
            raise ValueError("Use a valid analysis date in YYYY-MM-DD format.") from None
    run_id = payload.get("run_id")
    if run_id is not None and (type(run_id) is not int or run_id < 1):
        raise ValueError("The ingestion run must be a positive integer.")
    return question.strip(), options, analysis_date, run_id


def execute_request(payload):
    question, options, analysis_date, run_id = validate_request(payload)
    started = perf_counter()
    steps = []
    checked = check_input(question)
    if not checked["allowed"]:
        # Refusals work even before the database and model dependencies are set up.
        steps.append({
            "stage": "assistant.guard_input", "title": "Input safety check",
            "status": "blocked", "duration_ms": 0,
            "output": {"input_guardrail": checked},
        })
        result = {
            "status": "refused", "message": checked["reason"],
            "input_guardrail": checked, "research_only": True,
        }
    else:
        missing = configuration()["missing"]
        if missing:
            raise ValueError("Finish project setup in .env: " + ", ".join(missing) + ".")
        try:
            from assistant.graph import build_assistant

            assistant = build_assistant(**options, on_step=steps.append)
            result = assistant(question, analysis_date=analysis_date, run_id=run_id)
        except Exception as error:
            result = {
                "status": "error", "research_only": True,
                "message": "The assistant could not finish. Please try again later.",
                "error_type": type(error).__name__,
            }

        # Initialization failures happen before the nested graph can emit a node.
        for key, branch, title in (
            ("published_evidence", "evidence", "Published evidence pipeline"),
            ("synthetic_cohort", "cohort", "Synthetic cohort pipeline"),
        ):
            section = result.get(key, {})
            if section.get("status") == "error" and not any(
                step["stage"].startswith(branch + ".") and step["status"] == "error"
                for step in steps
            ):
                steps.append({
                    "stage": branch + ".initialization", "title": title,
                    "status": "error", "duration_ms": None,
                    "output": section,
                })

    return {
        "result": result,
        "flow": redact_blocked_outputs(steps),
        "features": {"hybrid": True, **options},
        "duration_ms": round((perf_counter() - started) * 1000, 2),
    }


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "ClinicalAssistant"
    sys_version = ""

    def send_body(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if os.getenv("VERCEL") == "1":
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, payload):
        body = json.dumps(redact_secrets(payload), ensure_ascii=False, default=str).encode("utf-8")
        self.send_body(status, body, "application/json; charset=utf-8")

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/status":
            state = configuration()
            if os.getenv("VERCEL") == "1":
                state.pop("missing", None)
            return self.send_json(HTTPStatus.OK, state)
        if path not in ASSETS:
            return self.send_json(HTTPStatus.NOT_FOUND, {"message": "Page not found."})
        filename, content_type = ASSETS[path]
        self.send_body(HTTPStatus.OK, (STATIC / filename).read_bytes(), content_type)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/ask":
            return self.send_json(HTTPStatus.NOT_FOUND, {"message": "Endpoint not found."})
        origin = self.headers.get("Origin")
        if (origin and (
            urlsplit(origin).netloc != self.headers.get("Host")
            or urlsplit(origin).scheme not in ("http", "https")
        )) or self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self.send_json(HTTPStatus.FORBIDDEN, {"message": "Run questions from this application."})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"message": "Send application/json."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 1 or length > 16384:
            return self.send_json(HTTPStatus.BAD_REQUEST, {"message": "Invalid request size."})
        try:
            payload = json.loads(self.rfile.read(length))
            validate_request(payload)
        except (ValueError, UnicodeError) as error:
            return self.send_json(HTTPStatus.BAD_REQUEST, {"message": str(error)})
        if not RUN_LOCK.acquire(blocking=False):
            return self.send_json(HTTPStatus.CONFLICT, {"message": "A pipeline is already running. Wait for it to finish."})
        try:
            try:
                consume_research_budget(self.headers)
            except ResearchLimit as error:
                return self.send_json(HTTPStatus.TOO_MANY_REQUESTS, {"message": str(error)})
            except Exception:
                return self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"message": "Research is temporarily unavailable. Please try again later."})
            try:
                response = execute_request(payload)
            except ValueError as error:
                message = "Research is temporarily unavailable." if os.getenv("VERCEL") == "1" else str(error)
                return self.send_json(HTTPStatus.BAD_REQUEST, {"message": message})
            self.send_json(HTTPStatus.OK, response)
        finally:
            RUN_LOCK.release()

    def log_message(self, format, *args):
        # Log request paths/statuses, never questions, credentials, or answers.
        super().log_message(format, *args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    load_settings()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), WorkbenchHandler)
    print(f"Research workbench: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
