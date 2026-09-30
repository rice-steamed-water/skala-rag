"""fixture ``research_company`` adapter. 네트워크 없이 준비한 bundle을 돌려준다.

contracts §7 ``ResearchCompany`` 경계를 만족한다. KRX·OpenDART 등 live adapter는
M2 범위다.

- 후보별 ``CompanyResearchBundle``을 미리 받아 ``ok``로 돌려준다.
- 준비한 bundle이 없는 후보는 ``TOOL_FAILED``다. 빈 profile을 만들어내지 않는다.
- 주입한 오류 코드면 ``unavailable``/``failed``로 구별한다.
- 호출 예산이 없으면 호출 전에 ``BUDGET_EXHAUSTED``로 끝낸다.
"""

from collections.abc import Mapping
from datetime import datetime

from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.interfaces import Clock
from skala_rag.contracts.retrieval import RetrievalRecord
from skala_rag.contracts.tools import CompanyResearchBundle, ToolBudget, ToolResult

TOOL_NAME = "fixture-research-company"
NODE = "company_research"
_CONTEXT = {"execution_mode": "fixture"}


class FixtureResearchCompany:
    """``ResearchCompany``. 호출마다 같은 bundle의 독립 복사본을 돌려준다.

    ``error_code``를 주면 bundle 대신 해당 도구 오류를 낸다(실패 경로 테스트용).
    """

    def __init__(
        self,
        bundles: Mapping[str, CompanyResearchBundle],
        *,
        run_id: str,
        schema_version: str,
        clock: Clock,
        error_code: ErrorCode | None = None,
    ) -> None:
        if error_code is not None and ERROR_SPECS[error_code].tool_status is None:
            raise ValueError(f"{error_code} is not a tool error code")
        for candidate_id, bundle in bundles.items():
            if bundle.profile.candidate_id != candidate_id:
                raise ValueError("bundle key와 profile.candidate_id 불일치")
            if any(e.candidate_id != candidate_id for e in bundle.evidence.values()):
                raise ValueError("다른 후보 근거가 섞인 bundle")
        self._bundles = dict(bundles)
        self._run_id = run_id
        self._schema_version = schema_version
        self._clock = clock
        self._error_code = error_code
        self.calls = 0

    def __call__(
        self, candidate: Candidate, budget: ToolBudget
    ) -> ToolResult[CompanyResearchBundle]:
        self.calls += 1
        started_at = self._clock.now()
        if budget.max_calls < 1:
            return self._failure(candidate, started_at, ErrorCode.BUDGET_EXHAUSTED)
        if self._error_code is not None:
            return self._failure(candidate, started_at, self._error_code)
        bundle = self._bundles.get(candidate.candidate_id)
        if bundle is None:
            return self._failure(candidate, started_at, ErrorCode.TOOL_FAILED)

        data = bundle.model_copy(deep=True)
        record = self._record(
            candidate,
            started_at,
            "ok",
            source_ids=list(data.sources),
            evidence_ids=list(data.evidence),
            error_id=None,
        )
        # fixture:// 출처는 명시적 fixture 검증 context가 있어야 재검증을 통과한다.
        return ToolResult[CompanyResearchBundle].model_validate(
            {
                "schema_version": self._schema_version,
                "status": "ok",
                "data": data,
                "retrieval_records": [record],
                "errors": [],
            },
            context=_CONTEXT,
        )

    def _retrieval_id(self) -> str:
        return f"retrieval-{TOOL_NAME}-{self._run_id}-{self.calls}"

    def _record(
        self,
        candidate: Candidate,
        started_at: datetime,
        status: str,
        *,
        source_ids: list[str],
        evidence_ids: list[str],
        error_id: str | None,
    ) -> RetrievalRecord:
        return RetrievalRecord(
            schema_version=self._schema_version,
            retrieval_id=self._retrieval_id(),
            run_id=self._run_id,
            candidate_id=candidate.candidate_id,
            tool_name=TOOL_NAME,
            query=candidate.canonical_name,
            arguments_without_secrets={"execution_mode": "fixture"},
            started_at=started_at,
            finished_at=self._clock.now(),
            status=status,
            source_ids=source_ids,
            chunk_ids=[],
            evidence_ids=evidence_ids,
            error_id=error_id,
            cost=None,
            cache_hit=False,
        )

    def _failure(
        self, candidate: Candidate, started_at: datetime, code: ErrorCode
    ) -> ToolResult[CompanyResearchBundle]:
        spec = ERROR_SPECS[code]
        error = WorkflowError(
            schema_version=self._schema_version,
            error_id=f"error-{self._retrieval_id()}",
            run_id=self._run_id,
            candidate_id=candidate.candidate_id,
            node=NODE,
            error_code=code.value,
            message_redacted=f"fixture company research {code}",
            retryable=spec.retryable,
            attempt=1,
            timestamp=started_at,
        )
        record = self._record(
            candidate,
            started_at,
            spec.tool_status,
            source_ids=[],
            evidence_ids=[],
            error_id=error.error_id,
        )
        return ToolResult[CompanyResearchBundle](
            schema_version=self._schema_version,
            status=spec.tool_status,
            data=None,
            retrieval_records=[record],
            errors=[error],
        )
