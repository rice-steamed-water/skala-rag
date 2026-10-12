"""예제 명령행의 종료 코드를 실제 subprocess와 통제된 결과로 확인한다."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Literal

import pytest
from examples import company_report_run, import_company_pdfs
from tests.integration.test_company_report import Harness
from tests.integration.test_company_report import offline as offline

from skala_rag.contracts.company_report import (
    CompanyReportConfigError,
    ImportFile,
    ImportState,
    ImportTarget,
)
from skala_rag.rag.manual_pdf import ImportResult

ROOT = Path(__file__).resolve().parents[2]


def _run(script: str, *args: str, env: dict[str, str] | None = None):
    return subprocess.run(
        [sys.executable, str(ROOT / "examples" / script), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
        timeout=120,
    )


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--company", "Synthetic Robotics"],
        ["--company", "Synthetic Robotics", "--base-config", "missing.json"],
        ["--company", "a/b", "--base-config", "missing.json"],
    ],
)
def test_import_cli_input_errors_exit_nonzero_without_traceback(tmp_path, args):
    # Given 인자 누락, 없는 config, 경로 구분자가 든 기업 이름.
    # When 실제 프로세스로 실행하면
    done = _run(
        "import_company_pdfs.py",
        *args,
        "--raw-root",
        str(tmp_path / "raw"),
        "--output-root",
        str(tmp_path / "output"),
    )
    # Then 종료 코드 2이고 예외 추적 없이 기계 코드만 출력한다.
    assert done.returncode == 2
    assert "Traceback" not in done.stderr


def _state(
    status: Literal["importing", "ready", "empty", "failed"],
    files: tuple[ImportFile, ...],
) -> ImportState:
    return ImportState(
        schema_version="manual-import-1",
        generation_id="g1",
        status=status,
        target=ImportTarget(subject_id="operator-x", name="Co", raw_dir=Path("/x")),
        input_fingerprint=None,
        recipe_hash=None,
        files=files,
        snapshot=None,
    )


@pytest.mark.parametrize(
    ("status", "config", "code"),
    [("empty", None, 1), ("failed", None, 1), ("importing", None, 1)],
)
def test_import_cli_not_ready_states_exit_one(
    monkeypatch, capsys, status, config, code
):
    # Given importer가 ready가 아닌 상태를 돌려준다.
    failed = ImportFile(
        relative_path="a.pdf",
        pdf_hash=None,
        status="failed",
        source_id=None,
        error_code="PDF_UNREADABLE",
    )
    result = ImportResult(
        state=_state(status, (failed,)), config_path=config, reused=False
    )
    monkeypatch.setattr(import_company_pdfs, "import_company_pdfs", lambda **_: result)
    # When CLI를 실행하면
    exit_code = import_company_pdfs.main(["--company", "Co", "--base-config", "b.json"])
    # Then 비정상 종료하고 실패 파일 사유가 stdout에 있다.
    assert exit_code == code
    assert "code=PDF_UNREADABLE" in capsys.readouterr().out


def test_import_cli_defaults_follow_guide(monkeypatch):
    # Given 인자 전달을 기록하는 importer
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return ImportResult(state=_state("empty", ()), config_path=None, reused=False)

    monkeypatch.setattr(import_company_pdfs, "import_company_pdfs", fake)
    # When raw/output 경로를 생략하면
    import_company_pdfs.main(["--company", "Synthetic Robotics", "--base-config", "b"])
    # Then 가이드의 기본 경로가 그대로 전달된다.
    assert seen["raw_root"] == Path("raw")
    assert seen["output_root"] == Path("data/local/manual")
    assert seen["company_name"] == "Synthetic Robotics"


def test_report_main_requires_environment_and_config_exit_two(tmp_path):
    # Given 환경 변수가 없거나 없는 설정 파일
    missing = _run("company_report_run.py", env={"PATH": "/usr/bin:/bin"})
    env = {
        "PATH": "/usr/bin:/bin",
        "SKALA_COMPANY_NAME": "Synthetic Robotics",
        "SKALA_COMPANY_REPORT_CONFIG": str(tmp_path / "missing.json"),
        "SKALA_COMPANY_REPORT_OUTPUT": str(tmp_path / "out"),
    }
    bad = _run("company_report_run.py", env=env)
    # Then 둘 다 종료 코드 2이며 추적 없이 끝난다.
    assert missing.returncode == bad.returncode == 2
    assert "Traceback" not in missing.stderr + bad.stderr


def _env(monkeypatch, config: Path, out: Path) -> None:
    monkeypatch.setenv("SKALA_COMPANY_NAME", "Synthetic a Robotics")
    monkeypatch.setenv("SKALA_COMPANY_REPORT_CONFIG", str(config))
    monkeypatch.setenv("SKALA_COMPANY_REPORT_OUTPUT", str(out))


def test_report_main_success_uses_today_and_exits_zero(tmp_path, monkeypatch, capsys):
    # Given 통제된 모델 wire와 실제 SQLite 저장소
    case = Harness(tmp_path / "ok", monkeypatch)
    _env(monkeypatch, case.config, case.root / "main")
    calls = []
    real = company_report_run.run_company_report
    monkeypatch.setattr(
        company_report_run,
        "run_company_report",
        lambda *a, **k: calls.append(k) or real(*a, **k),
    )
    # When 예제 main을 실행하면
    exit_code = company_report_run.main()
    # Then 기준일을 고정하지 않고 검증 통과 영수증을 출력해 0으로 끝난다.
    assert exit_code == 0
    assert "as_of" not in calls[0]
    printed = json.loads(capsys.readouterr().out)
    assert printed["outcome"] in ("completed", "partial")


def test_report_main_failed_receipt_shows_outcome_reasons_and_path(
    tmp_path, monkeypatch, capsys
):
    # Given 실패 영수증을 남기는 runner
    case = Harness(tmp_path / "bad", monkeypatch)
    out = case.root / "failed"
    _env(monkeypatch, case.config, out)

    def blocked(**_):
        raise CompanyReportConfigError("GRAPH_BLOCKED")

    monkeypatch.setattr("skala_rag.company_report.run_narrative_graph", blocked)
    # When 예제 main을 실행하면
    exit_code = company_report_run.main()
    # Then 비정상 종료하고 영수증의 결과·사유·경로가 stderr에 있다.
    receipt = json.loads((out / "run-result.json").read_text())
    err = capsys.readouterr().err
    assert exit_code == 1 and receipt["outcome"] != "completed"
    assert receipt["reason_codes"][0] in err
    assert str(out / "run-result.json") in err
