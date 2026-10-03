"""Local HTTP integration tests with explicit fake runners; no live model calls."""

import hashlib
import importlib
import json
import threading
import time
from contextlib import contextmanager
from http.client import HTTPConnection
from uuid import uuid4

import pytest


def forbidden_runner(**kwargs):
    raise ValueError("explicit test runner; no external API")


@contextmanager
def running(tmp_path, runner=None):
    module = importlib.import_module("skala_rag.demo_web")
    server = module.create_server(tmp_path, port=0, runner=runner or forbidden_runner)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(server, path="/", method="GET", body=None, headers=None):
    connection = HTTPConnection(*server.server_address, timeout=3)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def post(server, company="Physical Intelligence", **overrides):
    token = json.loads(request(server, "/api/bootstrap")[2])["csrf_token"]
    headers = {
        "Origin": f"http://127.0.0.1:{server.server_port}",
        "X-CSRF-Token": token,
        "Content-Type": "application/json",
    }
    headers.update(overrides)
    return request(
        server, "/api/runs", "POST", json.dumps({"company": company}), headers
    )


def wait_job(server, job_id):
    for _ in range(100):
        status, _, body = request(server, f"/api/runs/{job_id}")
        assert status == 200
        job = json.loads(body)
        if job["status"] != "running":
            return job
        time.sleep(0.01)
    pytest.fail("fake run did not finish")


def fake_output(root, status="completed"):
    run_id = str(uuid4())
    folder = root / "outputs" / run_id
    folder.mkdir(parents=True)
    artifacts = {
        "report.html": b"<!DOCTYPE html><h1>Fake report</h1>",
        "report.pdf": b"%PDF-1.4 fake test PDF",
        "evidence.json": b'{"e1": {"text": "fake evidence"}}',
        "sources.json": b'{"s1": {"title": "fake source"}}',
    }
    for name, data in artifacts.items():
        (folder / name).write_bytes(data)
    receipt = {
        "status": status,
        "run_id": run_id,
        "elapsed_seconds": 0.01,
        "publication_allowed": False,
        "artifact_hashes": {
            k: hashlib.sha256(v).hexdigest() for k, v in artifacts.items()
        },
    }
    (folder / "run-result.json").write_text(json.dumps(receipt))
    return folder


def test_background_generation_receipt_and_artifacts(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def runner(*, root, company, progress):
        calls.append(company)
        progress("research")
        entered.set()
        assert release.wait(3)
        return fake_output(root)

    with running(tmp_path, runner) as server:
        status, _, body = post(server)
        assert status == 202
        job_id = json.loads(body)["id"]
        assert entered.wait(1)
        pending = json.loads(request(server, f"/api/runs/{job_id}")[2])
        assert pending["status"] == "running" and pending["stage"] == "research"
        assert post(server)[0] == 409
        release.set()
        job = wait_job(server, job_id)
        assert job["status"] == "completed"
        assert job["receipt"]["publication_allowed"] is False
        assert job["receipt"]["elapsed_seconds"] == 0.01
        assert request(server, f"/api/runs/{job_id}/artifacts/report.pdf")[0] == 200
        for name in ("report.html", "evidence.json", "sources.json"):
            assert request(server, f"/api/runs/{job_id}/artifacts/{name}")[0] == 200
        next_id = json.loads(post(server, "피지컬 인텔리전스")[2])["id"]
        assert next_id != job_id
        next_job = wait_job(server, next_id)
        assert next_job["receipt"]["run_id"] != job["receipt"]["run_id"]
        assert len(calls) == 2


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.test"},
        {"Host": "localhost:8765"},
        {"Origin": "https://evil.test"},
        {"Origin": "null"},
        {"X-CSRF-Token": "wrong"},
        {"Origin": ""},
    ],
)
def test_rejects_untrusted_request_headers(tmp_path, headers):
    with running(tmp_path) as server:
        assert post(server, **headers)[0] == 403
        assert request(server, headers={"Host": "evil.test"})[0] == 403
        assert (
            request(server, "/api/bootstrap", headers={"Host": "evil.test"})[0] == 403
        )


@pytest.mark.parametrize("company", ["Other", "", ["Physical Intelligence"], None])
def test_rejects_unsupported_company(tmp_path, company):
    with running(tmp_path) as server:
        assert post(server, company)[0] == 400


def test_bad_request_json_and_size_are_bounded(tmp_path):
    with running(tmp_path) as server:
        token = json.loads(request(server, "/api/bootstrap")[2])["csrf_token"]
        headers = {
            "Origin": f"http://127.0.0.1:{server.server_port}",
            "X-CSRF-Token": token,
            "Content-Type": "application/json",
        }
        for body in ("not json", "[]", "null", "{}", '{"company": true}'):
            assert request(server, "/api/runs", "POST", body, headers)[0] == 400
        assert request(server, "/api/runs", "POST", "x" * 4097, headers)[0] == 413
        headers["Content-Type"] = "text/plain"
        assert request(server, "/api/runs", "POST", "{}", headers)[0] == 415


@pytest.mark.parametrize("tamper", ["bytes", "symlink", "receipt"])
def test_artifact_integrity_is_checked_on_every_read(tmp_path, tamper):
    folder = fake_output(tmp_path)
    with running(tmp_path, lambda **kwargs: folder) as server:
        job_id = json.loads(post(server)[2])["id"]
        wait_job(server, job_id)
        url = f"/api/runs/{job_id}/artifacts/report.pdf"
        assert request(server, url)[0] == 200
        if tamper == "bytes":
            (folder / "report.pdf").write_bytes(b"changed")
        elif tamper == "symlink":
            outside = tmp_path / "outside.pdf"
            outside.write_bytes((folder / "report.pdf").read_bytes())
            (folder / "report.pdf").unlink()
            (folder / "report.pdf").symlink_to(outside)
        else:
            receipt = json.loads((folder / "run-result.json").read_text())
            receipt["status"] = "warning"
            (folder / "run-result.json").write_text(json.dumps(receipt))
        assert request(server, url)[0] == 409


@pytest.mark.parametrize("status", ["warning", "failed"])
def test_noncompleted_receipt_never_serves_pdf(tmp_path, status):
    with running(tmp_path, lambda **kwargs: fake_output(tmp_path, status)) as server:
        job_id = json.loads(post(server)[2])["id"]
        assert wait_job(server, job_id)["status"] == status
        assert request(server, f"/api/runs/{job_id}/artifacts/report.pdf")[0] == 409


def test_paths_and_internal_files_are_not_exposed(tmp_path):
    folder = fake_output(tmp_path)
    (folder / ".env").write_text("secret")
    with running(tmp_path, lambda **kwargs: folder) as server:
        job_id = json.loads(post(server)[2])["id"]
        wait_job(server, job_id)
        for name in (
            ".env",
            "run-result.json",
            "report.md",
            "..",
            "%2e%2e",
            "../report.pdf",
            "%2e%2e%2f.env",
            "report.pdf?download=1",
        ):
            assert request(server, f"/api/runs/{job_id}/artifacts/{name}")[0] == 404
        for path in ("/.env", "/outputs/", "/../.env", "/api/runs/unknown"):
            assert request(server, path)[0] == 404
        _, headers, _ = request(server, f"/api/runs/{job_id}/artifacts/report.html")
        assert "script-src 'none'" in headers["Content-Security-Policy"]
        assert headers["Content-Type"] == "text/html; charset=utf-8"
        _, pdf_headers, _ = request(server, f"/api/runs/{job_id}/artifacts/report.pdf")
        assert pdf_headers["Content-Type"] == "application/pdf"
        # HTML sandboxing blocks native PDF plugins; only HTML gets a sandbox.
        assert "sandbox" not in pdf_headers["Content-Security-Policy"]


@pytest.mark.parametrize("kind", ["exception", "prerequisite", "bad_receipt"])
def test_failures_are_redacted_and_release_generation_lock(tmp_path, kind):
    def runner(**kwargs):
        if kind == "exception":
            raise RuntimeError("SECRET raw provider error")
        if kind == "prerequisite":
            raise ValueError("SECRET missing prerequisite path")
        folder = fake_output(tmp_path)
        (folder / "run-result.json").write_text('{"status": "invented"}')
        return folder

    with running(tmp_path, runner) as server:
        job_id = json.loads(post(server)[2])["id"]
        result = wait_job(server, job_id)
        assert result["status"] == "failed"
        assert "SECRET" not in json.dumps(result)
        assert result["error_code"] in ("run_failed", "prerequisites_missing")
        assert post(server)[0] == 202


def test_page_exposes_safe_accessible_research_workflow(tmp_path):
    with running(tmp_path) as server:
        html = request(server)[2].decode()
        for marker in (
            'name="viewport"',
            'aria-live="polite"',
            'id="company"',
            'id="run"',
            'sandbox="allow-same-origin"',
            'id="pdf-download"',
            'id="pdf-view"',
            'id="evidence"',
            'id="sources"',
            "textContent",
            "publication_allowed",
            "점수",
            "투자 추천",
            "warning",
            "failed",
            "completed",
        ):
            assert marker in html
        assert "innerHTML" not in html
        assert "allow-scripts" not in html
        assert "https://" not in html


def test_runner_receipt_preserves_prefixed_run_identifier(tmp_path):
    folder = fake_output(tmp_path)
    receipt = json.loads((folder / "run-result.json").read_text())
    receipt["run_id"] = "demo180-" + uuid4().hex
    (folder / "run-result.json").write_text(json.dumps(receipt))
    with running(tmp_path, lambda **kwargs: folder) as server:
        job_id = json.loads(post(server)[2])["id"]
        job = wait_job(server, job_id)
        assert job["status"] == "completed"
        assert job["receipt"]["run_id"] == receipt["run_id"]


def test_server_restart_never_generates_or_restores_jobs(tmp_path):
    calls = []

    def runner(**kwargs):
        calls.append(True)
        return fake_output(tmp_path)

    with running(tmp_path, runner) as server:
        assert request(server)[0] == 200
        assert calls == []
        job_id = json.loads(post(server)[2])["id"]
        wait_job(server, job_id)
    with running(tmp_path, runner) as server:
        assert request(server)[0] == 200
        assert request(server, f"/api/runs/{job_id}")[0] == 404
        assert json.loads(request(server, "/api/bootstrap")[2])["active_run_id"] is None
        assert calls == [True]


@pytest.mark.browser
@pytest.mark.parametrize("outcome", ["completed", "warning", "failed"])
def test_browser_runs_fake_research_and_renders_safe_details(tmp_path, outcome):
    from playwright.sync_api import Error, expect, sync_playwright

    entered, release = threading.Event(), threading.Event()

    def runner(**kwargs):
        entered.set()
        assert release.wait(10)
        folder = fake_output(tmp_path, outcome)
        evidence = b'{"e1":{"text":"<img src=x onerror=alert(1)>"}}'
        (folder / "evidence.json").write_bytes(evidence)
        receipt = json.loads((folder / "run-result.json").read_text())
        receipt["artifact_hashes"]["evidence.json"] = hashlib.sha256(
            evidence
        ).hexdigest()
        (folder / "run-result.json").write_text(json.dumps(receipt))
        return folder

    with running(tmp_path, runner) as server, sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Error as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}")
            expect(page.locator("#run")).to_be_enabled()
            for width in (1440, 768, 375):
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
            page.locator("#run").click()
            assert entered.wait(1)
            expect(page.locator("#run")).to_be_disabled()
            release.set()
            expect(page.locator("#status-box")).to_have_attribute(
                "data-state", outcome, timeout=10000
            )
            expect(page.locator("#run")).to_be_enabled()
            expect(page.locator("#publication")).to_contain_text("허용되지 않음")
            if outcome == "completed":
                expect(page.locator("#pdf-actions")).to_be_visible()
                expect(page.frame_locator("#report").locator("h1")).to_have_text(
                    "Fake report"
                )
            else:
                expect(page.locator("#pdf-actions")).to_be_hidden()
            page.get_by_text("근거 데이터 / Evidence", exact=True).click()
            expect(page.locator("#evidence")).to_contain_text("<img src=x")
            assert page.locator("#evidence img").count() == 0
            assert errors == []
        finally:
            release.set()
            browser.close()


@contextmanager
def rendered_evidence_page(tmp_path, evidence, sources):
    """Exercise the real page in Chromium with synthetic artifact responses."""
    from playwright.sync_api import expect, sync_playwright

    with running(tmp_path) as server, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.route(
                "**/artifacts/evidence.json",
                lambda route: route.fulfill(json=evidence),
            )
            page.route(
                "**/artifacts/sources.json",
                lambda route: route.fulfill(json=sources),
            )
            page.goto(f"http://127.0.0.1:{server.server_port}")
            expect(page.locator("#run")).to_be_enabled()
            page.evaluate(
                """() => showResult({id: 'synthetic', status: 'completed', receipt: {
                    company: 'Physical Intelligence', run_id: 'synthetic',
                    elapsed_seconds: 1, publication_allowed: false,
                    artifact_hashes: {'evidence.json': 'mock', 'sources.json': 'mock'}
                }})"""
            )
            yield page
        finally:
            browser.close()


@pytest.mark.browser
def test_browser_readable_evidence_distinguishes_shared_source(tmp_path):
    from playwright.sync_api import expect

    evidence = {
        "e1": {
            "source_id": "s1",
            "excerpt": "Reported robot performance.",
            "locator": "https://example.org/paper.pdf#page=2",
            "evidence_kind": "reported",
            "limitations": ["Text only."],
        },
        "e2": {
            "source_id": "s1",
            "excerpt": "Interpretation of the results.",
            "locator": "Section 3",
            "evidence_kind": "interpretation",
            "limitations": ["Not independently verified."],
        },
    }
    sources = {"s1": {"title": "Robot paper", "url": "https://example.org/paper.pdf"}}
    with rendered_evidence_page(tmp_path, evidence, sources) as page:
        expect(page.locator("#report-heading")).to_contain_text("Physical Intelligence")
        expect(page.locator("#evidence-summary")).to_have_text(
            "근거 2개 · 고유 출처 1개"
        )
        cards = page.locator("#evidence-list article")
        expect(cards).to_have_count(2)
        expect(cards.nth(0)).to_be_visible()
        expect(page.locator("#details")).to_contain_text("인용 번호와 다릅니다")
        for text in (
            "검색 근거 1",
            "Reported robot performance.",
            "페이지 2",
            "reported",
            "Text only.",
        ):
            expect(cards.nth(0)).to_contain_text(text)
        expect(cards.nth(0).get_by_role("link", name="Robot paper")).to_have_attribute(
            "href", "https://example.org/paper.pdf"
        )
        for text in (
            "근거 2",
            "Section 3",
            "interpretation",
            "Not independently verified.",
        ):
            expect(cards.nth(1)).to_contain_text(text)
        expect(page.locator("#source-list article")).to_have_count(1)
        expect(page.locator("#evidence")).to_be_hidden()
        page.get_by_text("근거 데이터 / Evidence", exact=True).click()
        expect(page.locator("#evidence")).to_contain_text("Reported robot performance.")
        for width in (1440, 768, 375):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )


@pytest.mark.browser
@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "data:text/html,unsafe", "file:///etc/passwd"]
)
def test_browser_evidence_untrusted_content_is_text_and_links_are_safe(tmp_path, url):
    from playwright.sync_api import expect

    payload = '<img src=x onerror="window.injected=true">'
    evidence = {
        "e1": {
            "source_id": "s1",
            "excerpt": payload,
            "claim": payload,
            "locator": url,
            "evidence_kind": "reported",
            "limitations": [payload],
        }
    }
    sources = {"s1": {"title": payload, "url": url, "access_notes": payload}}
    with rendered_evidence_page(tmp_path, evidence, sources) as page:
        expect(page.locator("#evidence-list blockquote")).to_have_text(payload)
        assert page.locator("#details img, #details script, #details a").count() == 0
        assert page.evaluate("window.injected === undefined")
        expect(page.locator("#source-list")).to_contain_text(payload)


@pytest.mark.browser
def test_browser_artifact_failure_is_visible_without_opening_raw_json(tmp_path):
    from playwright.sync_api import expect

    with rendered_evidence_page(tmp_path, {}, {}) as page:
        page.route(
            "**/artifacts/sources.json",
            lambda route: route.fulfill(status=409, body="unavailable"),
        )
        page.evaluate("""() => showResult({
            id: 'synthetic', status: 'warning', receipt: {
            company: 'Physical Intelligence', artifact_hashes: {
                'sources.json': 'mock', 'evidence.json': 'mock'
            }
        }})""")
        expect(page.locator("#artifact-error")).to_be_visible()
        expect(page.locator("#artifact-error")).to_contain_text("sources.json")
        expect(page.locator("#evidence-summary")).to_contain_text("고유 출처 확인 불가")
        page.evaluate("clearResult()")
        expect(page.locator("#report-heading")).to_have_text("리서치 보고서")
        assert page.locator("#evidence-list article").count() == 0


def test_loopback_page_and_bootstrap(tmp_path):
    with running(tmp_path) as server:
        status, headers, body = request(server)
        assert status == 200
        assert 'lang="ko"' in body.decode()
        assert "no-store" in headers["Cache-Control"]
        assert "Content-Security-Policy" in headers
        status, _, payload = request(server, "/api/bootstrap")
        assert status == 200
        token = json.loads(payload)["csrf_token"]
        assert len(token) >= 32 and token.encode() in body
    module = importlib.import_module("skala_rag.demo_web")
    for host in ("0.0.0.0", "localhost", "::", "example.com"):
        with pytest.raises(ValueError):
            module.create_server(tmp_path, host=host, port=0)
