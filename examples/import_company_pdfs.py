"""운영자가 넣은 기업 PDF 폴더를 가져오는 얇은 명령행 래퍼.

읽는 순서: 이 파일 → skala_rag/rag/manual_pdf.py(import_company_pdfs).
변환·분할·색인·상태 발행은 모두 importer가 하며, 이 파일은 인자를 넘기고
결과 상태를 요약해 종료 코드로 바꾸는 일만 한다. 비밀값과 PDF 본문은 출력하지 않는다.

종료 코드: 0 = ready(일부 PDF 실패 포함), 1 = empty/failed/importing 상태,
2 = 인자·설정·입력 폴더 오류.
"""

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from skala_rag.contracts.company_report import CompanyReportConfigError
from skala_rag.rag.manual_pdf import ManualImportError, import_company_pdfs

EXIT_OK, EXIT_IMPORT_NOT_READY, EXIT_BAD_INPUT = 0, 1, 2


def _parser() -> argparse.ArgumentParser:
    """기본 경로는 가이드와 같이 저장소 루트 기준 raw/ 와 data/local/manual/ 이다."""
    parser = argparse.ArgumentParser(
        description="기업 폴더의 PDF를 가져와 config를 만든다."
    )
    parser.add_argument("--company", required=True, help="raw-root 바로 아래 폴더 이름")
    parser.add_argument("--base-config", required=True, type=Path)
    parser.add_argument("--raw-root", type=Path, default=Path("raw"))
    parser.add_argument("--output-root", type=Path, default=Path("data/local/manual"))
    return parser


def main(argv: list[str] | None = None) -> int:
    """importer를 한 번 호출하고 상태를 요약해 종료 코드를 돌려준다."""
    args = _parser().parse_args(argv)
    try:
        result = import_company_pdfs(
            company_name=args.company,
            raw_root=args.raw_root,
            output_root=args.output_root,
            base_config_path=args.base_config,
        )
    except ManualImportError as exc:
        print(f"IMPORT_REJECTED code={exc.code}", file=sys.stderr)
        return EXIT_BAD_INPUT
    except (CompanyReportConfigError, ValidationError, OSError, ValueError) as exc:
        # 설정·입력 오류는 기계 코드가 있으면 코드만, 없으면 예외 종류만 보인다.
        code = getattr(exc, "code", type(exc).__name__)
        print(f"IMPORT_CONFIG_ERROR code={code}", file=sys.stderr)
        return EXIT_BAD_INPUT
    state = result.state
    counts = {
        status: sum(item.status == status for item in state.files)
        for status in ("ok", "empty", "failed")
    }
    print(
        f"status={state.status} generation={state.generation_id} "
        f"reused={result.reused} discovered={len(state.files)} "
        f"ok={counts['ok']} empty={counts['empty']} failed={counts['failed']}"
    )
    # 실패·빈 파일은 경로와 오류 코드만 출력한다. 본문은 출력하지 않는다.
    for item in state.files:
        if item.status != "ok":
            print(
                f"file={item.relative_path} status={item.status} code={item.error_code}"
            )
    if state.status == "ready" and result.config_path is not None:
        print(f"config_path={result.config_path}")
        return EXIT_OK
    print("config_path=none", file=sys.stderr)
    return EXIT_IMPORT_NOT_READY


if __name__ == "__main__":
    raise SystemExit(main())
