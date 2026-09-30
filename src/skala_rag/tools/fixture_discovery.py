"""fixture ``search_candidates`` adapter. 네트워크 없이 준비한 bundle을 돌려준다.

contracts §7 ``SearchCandidates`` 경계를 만족한다. live 검색 adapter는 M2 범위다.

- 후보가 있으면 ``ok``, 0건이면 ``empty``(오류 아님), 주입한 오류 코드면
  ``unavailable``/``failed``로 구별한다.
- ``execution_mode``가 fixture가 아니면 ``TOOL_NOT_CONFIGURED``로 거절한다.
- 호출 예산이 없으면 호출 전에 ``BUDGET_EXHAUSTED``로 끝낸다.
"""

from skala_rag.contracts.bundles import DiscoveryBundle
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.inputs import RunInput
from skala_rag.contracts.interfaces import Clock
from skala_rag.contracts.retrieval import RetrievalRecord
from skala_rag.contracts.tools import ToolBudget, ToolResult

TOOL_NAME = "fixture-search-candidates"
NODE = "discovery"


class FixtureSearchCandidates:
    """``SearchCandidates``. 호출마다 같은 bundle의 독립 복사본을 돌려준다.

    ``error_code``를 주면 bundle 대신 해당 도구 오류를 낸다(실패 경로 테스트용).
    """

    def __init__(
        self,
        bundle: DiscoveryBundle,
        *,
        run_id: str,
        clock: Clock,
        error_code: ErrorCode | None = None,
    ) -> None:
        if error_code is not None and ERROR_SPECS[error_code].tool_status is None:
            raise ValueError(f"{error_code} is not a tool error code")
        self._bundle = bundle
        self._run_id = run_id
        self._clock = clock
        self._error_code = error_code
        self.calls = 0

    def __call__(
        self, request: RunInput, budget: ToolBudget
    ) -> ToolResult[DiscoveryBundle]:
        self.calls += 1
        started_at = self._clock.now()
        if budget.max_calls < 1:
            return self._failure(request, started_at, ErrorCode.BUDGET_EXHAUSTED)
        if request.execution_mode != "fixture":
            return self._failure(request, started_at, ErrorCode.TOOL_NOT_CONFIGURED)
        if self._error_code is not None:
            return self._failure(request, started_at, self._error_code)

        data = self._bundle.model_copy(deep=True)
        status = "ok" if data.candidates else "empty"
        record = self._record(
            request, started_at, status, source_ids=list(data.sources), error_id=None
        )
        # fixture:// 출처는 명시적 fixture 검증 context가 있어야 재검증을 통과한다.
        return ToolResult[DiscoveryBundle].model_validate(
            {
                "schema_version": self._bundle.schema_version,
                "status": status,
                "data": data,
                "retrieval_records": [record],
                "errors": [],
            },
            context={"execution_mode": request.execution_mode},
        )

    def _retrieval_id(self) -> str:
        return f"retrieval-{TOOL_NAME}-{self._run_id}-{self.calls}"

    def _record(
        self,
        request: RunInput,
        started_at,
        status: str,
        *,
        source_ids: list[str],
        error_id: str | None,
    ) -> RetrievalRecord:
        return RetrievalRecord(
            schema_version=self._bundle.schema_version,
            retrieval_id=self._retrieval_id(),
            run_id=self._run_id,
            candidate_id=None,
            tool_name=TOOL_NAME,
            query=request.investment_theme,
            arguments_without_secrets={
                "execution_mode": request.execution_mode,
                "countries": list(request.countries),
                "languages": list(request.languages),
                "as_of": request.as_of.isoformat(),
            },
            started_at=started_at,
            finished_at=self._clock.now(),
            status=status,
            source_ids=source_ids,
            chunk_ids=[],
            evidence_ids=[],
            error_id=error_id,
            cost=None,
            cache_hit=False,
        )

    def _failure(
        self, request: RunInput, started_at, code: ErrorCode
    ) -> ToolResult[DiscoveryBundle]:
        spec = ERROR_SPECS[code]
        error = WorkflowError(
            schema_version=self._bundle.schema_version,
            error_id=f"error-{self._retrieval_id()}",
            run_id=self._run_id,
            candidate_id=None,
            node=NODE,
            error_code=code.value,
            message_redacted=f"fixture discovery {code}",
            retryable=spec.retryable,
            attempt=1,
            timestamp=started_at,
        )
        record = self._record(
            request,
            started_at,
            spec.tool_status,
            source_ids=[],
            error_id=error.error_id,
        )
        return ToolResult[DiscoveryBundle](
            schema_version=self._bundle.schema_version,
            status=spec.tool_status,
            data=None,
            retrieval_records=[record],
            errors=[error],
        )
