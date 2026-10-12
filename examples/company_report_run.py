"""기업명과 설정으로 보고서를 실행하는 Python 예제.

읽는 순서: 이 파일 → skala_rag/company_report.py → graph/company_report.py.
이 모듈을 import하는 것만으로는 보고서가 실행되지 않는다.
산출물 검사는 실제 모델 품질 평가와 별개이며, QA에는 준비된 로컬 모델이 필요하다.
합성 응답을 사용하는 전송 계층은 테스트 전용이다.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError
from pypdf import PdfReader

from skala_rag.company_report import run_company_report
from skala_rag.contracts.company_report import (
    CompanyReportConfigError,
    NarrativeRunResult,
    ResearchLimits,
    load_company_report_config,
)


def scenario_stored_report(
    company_name: str, *, config_path: str | Path, output_dir: str | Path
) -> Path:
    """신규 수집을 끄고 이미 저장된 근거로 보고서를 만든다."""
    # 문자열 경로도 받는 예제 함수지만, 공개 API에는 Path 객체로 전달한다.
    # research=False는 자료 수집만 끈다. 분석·작성 모델은 실제 호출될 수 있다.
    return run_company_report(
        company_name,
        config_path=Path(config_path),
        output_dir=Path(output_dir),
        research=False,
    )


def scenario_research_target_report(
    company_name: str,
    *,
    config_path: str | Path,
    output_dir: str | Path,
    research_limits: ResearchLimits,
) -> Path:
    """설정된 대상 기업에 한해 한도를 둔 선택적 조사를 요청한다."""
    # 한도는 조사에 사용할 상한이다. 이 값만으로 자료 수집 권한이 생기지는 않는다.
    return run_company_report(
        company_name,
        config_path=Path(config_path),
        output_dir=Path(output_dir),
        research=True,
        research_limits={
            "max_calls": research_limits.max_calls,
            "max_cost_usd": research_limits.max_cost_usd,
            "deadline_seconds": research_limits.deadline_seconds,
        },
    )


def _check_report(out: Path, *, research: bool) -> NarrativeRunResult:
    """저장된 결과와 검증 영수증을 확인한다. 실제 내용 품질을 재심사하지는 않는다."""
    # 공개 API는 결과 객체가 아니라 디렉터리를 반환한다.
    # 영수증을 DTO로 읽어 성공 상태와 검증 통과를 함께 확인한다.
    result = NarrativeRunResult.model_validate_json(
        (out / "run-result.json").read_bytes()
    )
    assert result.outcome in ("completed", "partial")
    assert result.validation == "passed"
    assert result.research_requested is research
    assert result.report_md_path == str(out / "report.md")
    assert result.pdf_path == str(out / "report.pdf")
    assert PdfReader(out / "report.pdf").pages
    # 해시는 영수증이 바로 이 실행의 Judge·PDF 검사·출처 파일을 가리키는지 확인한다.
    assert result.validation_receipt_hashes == [
        hashlib.sha256((out / name).read_bytes()).hexdigest()
        for name in ("judgment.json", "pdf-validation.json", "references.json")
    ]
    judgment = json.loads((out / "judgment.json").read_bytes())
    assert judgment["verdict"] == "pass" and not judgment["issues"]
    return result


def _require_model_assets(config_path: str | Path) -> None:
    """QA 실행 전에 로컬 모델 폴더와 검증 영수증의 존재를 확인한다."""
    config = load_company_report_config(Path(config_path))
    if not config.model_path.is_dir() or not config.model_receipt_path.is_file():
        raise CompanyReportConfigError("ACTUAL_QA_SCOPE_REQUIRED")


def qa_stored(
    company_name: str, *, config_path: str | Path, output_dir: str | Path
) -> Path:
    """로컬 모델 준비물을 확인한 뒤 저장 자료 기반 보고서를 실행·검사한다."""
    _require_model_assets(config_path)
    out = scenario_stored_report(
        company_name, config_path=config_path, output_dir=output_dir
    )
    _check_report(out, research=False)
    return out


def qa_research(
    company_name: str,
    *,
    config_path: str | Path,
    output_dir: str | Path,
    research_limits: ResearchLimits,
) -> Path:
    """로컬 모델과 명시적 조사 한도를 받아 조사 보고서를 실행·검사한다."""
    _require_model_assets(config_path)
    out = scenario_research_target_report(
        company_name,
        config_path=config_path,
        output_dir=output_dir,
        research_limits=research_limits,
    )
    _check_report(out, research=True)
    return out


def main() -> int:
    """환경 변수로 보고서를 실행하고 영수증에 맞는 종료 코드를 돌려준다.

    0 = 검증 통과한 completed/partial, 1 = 실패·빈 결과·검증 실패, 2 = 설정 오류.
    기준일은 지정하지 않아 실행 당일이 사용된다.
    """
    try:
        output = run_company_report(
            os.environ["SKALA_COMPANY_NAME"],
            config_path=Path(os.environ["SKALA_COMPANY_REPORT_CONFIG"]),
            output_dir=Path(os.environ["SKALA_COMPANY_REPORT_OUTPUT"]),
            research=False,
        )
    except KeyError as exc:
        print(f"ENV_REQUIRED name={exc.args[0]}", file=sys.stderr)
        return 2
    except (CompanyReportConfigError, OSError, ValidationError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        print(f"REPORT_CONFIG_ERROR code={code}", file=sys.stderr)
        return 2
    # 정상 종료나 PDF 파일 존재만으로 성공을 판단하지 않고 영수증까지 검사한다.
    result_path = output / "run-result.json"
    receipt = NarrativeRunResult.model_validate_json(result_path.read_bytes())
    if receipt.outcome not in ("completed", "partial"):
        # 실패 영수증의 결과·사유·경로를 그대로 보여 준다.
        print(
            f"REPORT_NOT_COMPLETED outcome={receipt.outcome} "
            f"reasons={','.join(receipt.reason_codes)} result={result_path}",
            file=sys.stderr,
        )
        return 1
    try:
        result = _check_report(output, research=False)
    except AssertionError:
        print(f"REPORT_VALIDATION_FAILED result={result_path}", file=sys.stderr)
        return 1
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
