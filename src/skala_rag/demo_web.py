"""Loopback-only research desk. No directory serving or startup generation."""

import hashlib
import json
import math
import os
import secrets
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

PAGE_CSP = (
    "default-src 'none'; script-src 'self' 'unsafe-inline'; "
    "style-src 'unsafe-inline'; connect-src 'self'; frame-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
REPORT_CSP = (
    "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
    "font-src data:; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'self'; sandbox allow-same-origin"
)
ARTIFACTS = {
    "report.html": "text/html; charset=utf-8",
    "report.pdf": "application/pdf",
    "evidence.json": "application/json",
    "sources.json": "application/json",
}


def _read_file(folder, name):
    """Read only a regular direct child, rejecting symlinks before opening."""
    if folder.resolve(strict=True) != folder:
        raise ValueError("Changed output directory")
    fd = os.open(folder / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Not a regular artifact")
        return stream.read()


def _receipt(folder):
    raw = _read_file(folder, "run-result.json")
    receipt = json.loads(raw)
    if (
        not isinstance(receipt, dict)
        or receipt.get("status") not in ("completed", "warning", "failed")
        or receipt.get("publication_allowed") is not False
        or not isinstance(receipt.get("artifact_hashes"), dict)
    ):
        raise ValueError("Invalid receipt")
    run_id = receipt.get("run_id")
    if not isinstance(run_id, str) or not run_id or len(run_id) > 200:
        raise ValueError("Invalid run identifier")
    elapsed = receipt["elapsed_seconds"]
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("Invalid elapsed time")
    # Never send arbitrary runner receipt fields or exception strings to clients.
    public = {
        key: receipt[key]
        for key in ("status", "run_id", "elapsed_seconds", "publication_allowed")
    }
    public["artifact_hashes"] = {
        key: value
        for key, value in receipt["artifact_hashes"].items()
        if key in ARTIFACTS
        and isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    }
    return public, hashlib.sha256(raw).hexdigest()


def create_server(
    root: Path, host="127.0.0.1", port=8765, runner=None
) -> ThreadingHTTPServer:
    """Build a server without starting it or performing provider calls."""
    if host != "127.0.0.1":
        raise ValueError("Only 127.0.0.1 is supported")
    root = Path(root).resolve()
    token = secrets.token_urlsafe(32)
    jobs = {}
    lock = threading.Lock()
    active = False

    def execute(job_id, company):
        nonlocal active

        def progress(stage):
            with lock:
                jobs[job_id]["stage"] = str(stage)[:200]

        try:
            selected_runner = runner
            if selected_runner is None:
                from skala_rag.local_demo import run_demo

                selected_runner = run_demo
            folder = Path(
                selected_runner(root=root, company=company, progress=progress)
            )
            folder = folder.absolute()
            if folder.resolve(strict=True) != folder or not folder.is_relative_to(root):
                raise ValueError("Invalid output directory")
            receipt, receipt_hash = _receipt(folder)
            result = {
                "status": receipt["status"],
                "stage": receipt["status"],
                "receipt": receipt,
                "folder": folder,
                "receipt_hash": receipt_hash,
            }
        except Exception:
            result = {"status": "failed", "stage": "failed", "error_code": "run_failed"}
        # Terminal status and the generation lock are published atomically.
        with lock:
            jobs[job_id].update(result)
            active = False

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            pass

        def respond(
            self, status, payload, content_type="application/json", csp=PAGE_CSP
        ):
            data = (
                json.dumps(payload).encode() if isinstance(payload, dict) else payload
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", csp)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def trusted(self, post=False):
            authority = f"127.0.0.1:{self.server.server_address[1]}"
            valid = self.headers.get_all("Host", []) == [authority]
            if post:
                valid = (
                    valid
                    and self.headers.get_all("Origin", []) == [f"http://{authority}"]
                    and self.headers.get_all("X-CSRF-Token", []) == [token]
                )
            if not valid:
                self.respond(403, {"error": "forbidden"})
            return valid

        def artifact(self, job, name):
            if name not in ARTIFACTS:
                self.respond(404, {"error": "not_found"})
                return
            try:
                folder = job["folder"]
                receipt, receipt_hash = _receipt(folder)
                if receipt_hash != job["receipt_hash"]:
                    raise ValueError("Receipt changed")
                if name == "report.pdf" and receipt["status"] != "completed":
                    raise ValueError("PDF is not validated")
                data = _read_file(folder, name)
                if hashlib.sha256(data).hexdigest() != receipt["artifact_hashes"][name]:
                    raise ValueError("Artifact changed")
            except (OSError, ValueError, KeyError, TypeError):
                self.respond(409, {"error": "artifact_unavailable"})
                return
            # Respond with exactly the bytes that were hashed, not a reopened file.
            csp = (
                REPORT_CSP
                if name == "report.html"
                else (
                    "default-src 'none'; script-src 'none'; base-uri 'none'; "
                    "frame-ancestors 'self'"
                )
            )
            self.respond(200, data, ARTIFACTS[name], csp)

        def do_GET(self):
            if not self.trusted():
                return
            if self.path == "/":
                page = Path(__file__).with_name("demo_page.html").read_text()
                self.respond(
                    200,
                    page.replace("__CSRF_TOKEN__", token).encode(),
                    "text/html; charset=utf-8",
                )
            elif self.path == "/api/bootstrap":
                with lock:
                    running_id = next(
                        (
                            key
                            for key, job in jobs.items()
                            if job["status"] == "running"
                        ),
                        None,
                    )
                self.respond(200, {"csrf_token": token, "active_run_id": running_id})
            elif self.path.startswith("/api/runs/"):
                parts = self.path.split("/")
                with lock:
                    job = dict(jobs.get(parts[3], {}))
                if not job:
                    self.respond(404, {"error": "not_found"})
                elif len(parts) == 4:
                    self.respond(
                        200,
                        {
                            k: v
                            for k, v in job.items()
                            if k not in ("folder", "receipt_hash")
                        },
                    )
                elif len(parts) == 6 and parts[4] == "artifacts":
                    self.artifact(job, parts[5])
                else:
                    self.respond(404, {"error": "not_found"})
            else:
                self.respond(404, {"error": "not_found"})

        def do_POST(self):
            nonlocal active
            if not self.trusted(post=True):
                return
            if self.path != "/api/runs":
                self.respond(404, {"error": "not_found"})
                return
            if self.headers.get_content_type() != "application/json":
                self.respond(415, {"error": "json_required"})
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                    raise ValueError
                length = int(lengths[0])
                if length < 0:
                    raise ValueError
                if length > 4096:
                    self.respond(413, {"error": "request_too_large"})
                    return
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or payload.get("company") not in (
                    "Physical Intelligence",
                    "피지컬 인텔리전스",
                ):
                    raise ValueError
            except (ValueError, UnicodeError, RecursionError, TimeoutError):
                self.respond(400, {"error": "invalid_request"})
                return
            with lock:
                if active:
                    self.respond(409, {"error": "busy"})
                    return
                active = True
                job_id = str(uuid4())
                jobs[job_id] = {"id": job_id, "status": "running", "stage": "starting"}
            threading.Thread(
                target=execute, args=(job_id, payload["company"]), daemon=True
            ).start()
            self.respond(202, {"id": job_id, "status": "running"})

    return ThreadingHTTPServer((host, port), Handler)


def serve(root=Path.cwd()):
    """Serve until interrupted; invoke directly from Python, without CLI flags."""
    server = create_server(root)
    try:
        server.serve_forever()
    finally:
        server.server_close()
