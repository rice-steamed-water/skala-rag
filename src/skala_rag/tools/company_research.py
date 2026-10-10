"""live ``research_company`` — #51, T04, contracts §7, data-rag §2·§6.

여러 provider(공식 홈페이지, OpenDART 등)의 관측을 하나의 ``CompanyResearchBundle``로
모은다. 적격성 판정은 하지 않는다(``agents.eligibility.check_eligibility`` 재사용).

- provider는 외부 요청마다 ``CallBudget``을 먼저 차감한다. 재시도는 하지 않는다(#45).
- 필수(required) provider의 조회 실패는 도구 결과 전체의 ``unavailable``/``failed``다.
  선택 provider 실패는 하위 RetrievalRecord로 남기고 계속한다. 정상 0건은 ``empty``다.
- 관측을 붙이는 조건: 법인 식별자 일치(``legal_identifier``) 또는 후보 homepage와
  같은 도메인의 원문(``official_domain``). 회사명만 맞는 관측(``name_only``)은 버린다.
- 기준일(``as_of``) 이후 공개·확보한 Source와 기준일 이후 사건은 버린다.
- 같은 조건의 값이 다르면: 모든 관측에 사건일이 있고 최신 사건일의 값이 하나면
  사건일 순으로 이전 근거를 대체(supersedes)한다. 아니면 서로 상충(conflicts_with)으로
  두고 profile 값을 null로 남긴다. 기사 발행일은 사건일로 쓰지 않는다.
- 근거가 없는 조건은 null/unknown이다. 0건을 비상장·Exit 없음으로 바꾸지 않는다.

버린 관측과 건너뛴 provider는 요약 RetrievalRecord의 ``arguments_without_secrets``에
사유 코드로 남긴다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from skala_rag.agents.eligibility import (
    FIELD_BUSINESS,
    FIELD_DOMAIN,
    FIELD_EXIT,
    FIELD_IDENTITY,
    FIELD_LISTING,
    FIELD_STAGE,
)
from skala_rag.contracts.candidates import Candidate, CompanyProfile, StageInfo
from skala_rag.contracts.common import Confidence, JSONMap
from skala_rag.contracts.error_codes import ERROR_SPECS, ErrorCode
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.evidence import Evidence, EvidenceProvenance
from skala_rag.contracts.ids import evidence_id, normalize_claim
from skala_rag.contracts.interfaces import Clock
from skala_rag.contracts.retrieval import RetrievalRecord
from skala_rag.contracts.sources import Source
from skala_rag.contracts.tools import CompanyResearchBundle, ToolBudget, ToolResult
from skala_rag.tools.source_fetch import check_as_of

TOOL_NAME = "company-research"
NODE = "company_research"

# ToolResult only accepts codes with an ERROR_SPECS tool_status. Preserve the
# original LLM WorkflowError in existing summary metadata for the State consumer.
EXTRACTOR_TOOL_ERRORS = {
    ErrorCode.LLM_TIMEOUT: ErrorCode.TOOL_TIMEOUT,
    ErrorCode.LLM_OUTPUT_INVALID: ErrorCode.TOOL_RESPONSE_INVALID,
    ErrorCode.LLM_FAILED: ErrorCode.TOOL_FAILED,
}

ToolStatus = Literal["ok", "empty", "unavailable", "failed"]
IdentityBasis = Literal["legal_identifier", "official_domain", "name_only"]
EvidenceKind = Literal["reported", "derived", "estimated"]

BOOLEAN_FIELDS = frozenset({FIELD_DOMAIN, FIELD_LISTING, FIELD_EXIT})
PRESENCE_FIELDS = frozenset({FIELD_IDENTITY, FIELD_BUSINESS})
RESEARCH_FIELDS = BOOLEAN_FIELDS | PRESENCE_FIELDS | {FIELD_STAGE}


def normalize_identifier(value: str) -> str:
    """비교용. 하이픈·공백을 뺀다(예: 사업자등록번호 ``123-45-67890``)."""
    return "".join(ch for ch in value if ch.isalnum()).lower()


def candidate_identifiers(candidate: Candidate) -> dict[str, str]:
    return {
        scheme.strip().lower(): normalize_identifier(value)
        for scheme, value in candidate.legal_identifiers.items()
        if normalize_identifier(value)
    }


def url_host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlsplit(url.strip()).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.lower().rstrip(".").removeprefix("www.")


def same_site(url: str | None, homepage_url: str | None) -> bool:
    """``url``이 후보 homepage와 같은 host이거나 그 하위 도메인인가."""
    host, home = url_host(url), url_host(homepage_url)
    if host is None or home is None:
        return False
    return host == home or host.endswith(f".{home}")


@dataclass(frozen=True)
class StageObservation:
    """추출기가 원문 표기와 함께 준 단계. 여기서 정규화하지 않는다(D06)."""

    raw_label: str | None
    normalized_round: Literal[
        "seed", "series_a", "series_b", "series_c", "out_of_scope", "unknown"
    ]
    method: Literal["explicit", "estimated"]


@dataclass(frozen=True)
class FieldObservation:
    """한 Source에서 얻은 적격성 조건 하나의 관측.

    ``value``: 상장·Exit·도메인은 bool, 단계는 ``StageObservation``, 식별·사업
    근거는 None(존재만 기록). ``event_date``는 사건일(라운드 종료일, 상장일 등)이며
    기사 발행일이 아니다. 발행일은 Source.published_at에 있다.
    """

    field: str
    value: bool | StageObservation | None
    source_id: str
    locator: str
    claim: str
    excerpt: str
    identity_basis: IdentityBasis
    matched_identifiers: Mapping[str, str] = field(default_factory=dict)
    event_date: date | None = None
    evidence_kind: EvidenceKind = "reported"
    confidence: Confidence = "unknown"
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.field not in RESEARCH_FIELDS:
            raise ValueError(f"unknown eligibility field {self.field!r}")
        if self.field in BOOLEAN_FIELDS and not isinstance(self.value, bool):
            raise ValueError(f"{self.field} requires a bool value")
        if self.field == FIELD_STAGE and not isinstance(self.value, StageObservation):
            raise ValueError("stage requires StageObservation")
        if self.field in PRESENCE_FIELDS and self.value is not None:
            raise ValueError(f"{self.field} records presence only")


@dataclass(frozen=True)
class ProviderCall:
    """외부 요청 1회. ``arguments``에 key·token을 넣지 않는다."""

    status: ToolStatus
    method: Literal["web", "api"]
    query: str | None
    arguments: JSONMap
    started_at: datetime
    finished_at: datetime
    source_ids: tuple[str, ...] = ()
    error_code: ErrorCode | None = None


@dataclass(frozen=True)
class ProviderOutcome:
    """provider 한 번의 결과.

    - ok/empty: ``error_code`` 없음. empty는 조회 성공·해당 자료 없음.
    - unavailable/failed: ``error_code``와 비밀 없는 ``message``.
    - ``skipped``: 요청 없이 건너뛴 사유(예: 식별자 없음, 지원 국가 아님). status는
      empty이며 자료 부재가 아니라 미조회다.
    - ``notes``: 동명 기업 불일치 등 사유 코드. 요약 기록에 남는다.
    """

    status: ToolStatus
    sources: tuple[Source, ...] = ()
    observations: tuple[FieldObservation, ...] = ()
    calls: tuple[ProviderCall, ...] = ()
    error_code: ErrorCode | None = None
    message: str | None = None
    skipped: str | None = None
    notes: tuple[str, ...] = ()
    extractor_error_code: ErrorCode | None = None

    def __post_init__(self) -> None:
        failed = self.status in ("unavailable", "failed")
        if failed != (self.error_code is not None):
            raise ValueError("error_code is required exactly for failed outcomes")
        if failed and ERROR_SPECS[self.error_code].tool_status != self.status:
            raise ValueError(f"{self.error_code} does not match {self.status}")
        if self.skipped is not None and self.status != "empty":
            raise ValueError("skipped outcome must be empty")


class CallBudget:
    """이번 research 호출의 외부 요청 한도. 요청 전에 ``take``로 차감한다."""

    def __init__(self, max_calls: int, *, clock: Clock, deadline: datetime | None):
        self.remaining = max_calls
        self.used = 0
        self._clock = clock
        self._deadline = deadline

    def take(self) -> bool:
        if self.remaining < 1:
            return False
        if self._deadline is not None and self._clock.now() >= self._deadline:
            return False
        self.remaining -= 1
        self.used += 1
        return True


@runtime_checkable
class CompanyResearchProvider(Protocol):
    name: str
    required: bool

    def __call__(self, candidate: Candidate, calls: CallBudget) -> ProviderOutcome: ...


def failed_outcome(
    code: ErrorCode, message: str, calls: Sequence[ProviderCall] = ()
) -> ProviderOutcome:
    return ProviderOutcome(
        status=ERROR_SPECS[code].tool_status,
        calls=tuple(calls),
        error_code=code,
        message=message,
    )


# ---------------------------------------------------------------- assemble


@dataclass(frozen=True)
class Rejection:
    field: str
    source_id: str
    reason: str


@dataclass
class _Accepted:
    observation: FieldObservation
    retrieval_id: str
    method: Literal["web", "api"]


def _identity_rejection(
    obs: FieldObservation, candidate: Candidate, source: Source
) -> str | None:
    if obs.identity_basis == "name_only":
        return "NAME_ONLY"
    if obs.identity_basis == "official_domain":
        if not same_site(source.url, candidate.homepage_url):
            return "DOMAIN_MISMATCH"
        return None
    known = candidate_identifiers(candidate)
    matched = {
        s.strip().lower(): normalize_identifier(v)
        for s, v in obs.matched_identifiers.items()
    }
    if not matched or any(not v for v in matched.values()):
        return "IDENTIFIER_MISSING"
    if any(scheme not in known for scheme in matched):
        return "IDENTIFIER_UNVERIFIABLE"
    if any(known[scheme] != value for scheme, value in matched.items()):
        return "IDENTIFIER_MISMATCH"
    return None


def _value_key(obs: FieldObservation):
    if isinstance(obs.value, StageObservation):
        return obs.value.normalized_round
    return obs.value


def _chronological(item: _Accepted):
    obs = item.observation
    return (obs.event_date is not None, obs.event_date or date.min, obs.source_id)


def _resolve(items: list[_Accepted]) -> tuple[str, list[_Accepted]]:
    """``same``/``supersede``/``conflict``와 사건일 순(미상 먼저) 목록."""
    ordered = sorted(items, key=_chronological)
    if len({_value_key(a.observation) for a in items}) <= 1:
        return "same", ordered
    if any(a.observation.event_date is None for a in items):
        return "conflict", ordered
    latest = ordered[-1].observation.event_date
    latest_values = {
        _value_key(a.observation) for a in ordered if a.observation.event_date == latest
    }
    if len(latest_values) > 1:
        return "conflict", ordered
    return "supersede", ordered


def _evidence(
    candidate_id: str,
    accepted: _Accepted,
    *,
    schema_version: str,
    supersedes: str | None,
    extra_limitations: Sequence[str] = (),
) -> Evidence:
    obs = accepted.observation
    claim = normalize_claim(obs.claim)
    core = dict(
        source_id=obs.source_id,
        locator=obs.locator,
        claim=claim,
        candidate_id=candidate_id,
        scope="company",
        value=None,
        unit=None,
        currency=None,
        value_as_of=None,
        period=None,
        geography=None,
        event_date=obs.event_date,
        evidence_kind=obs.evidence_kind,
        supporting_evidence_ids=[],
        derivation=None,
        supersedes=supersedes,
    )
    return Evidence(
        schema_version=schema_version,
        evidence_id=evidence_id(**core),
        criterion_ids=[],
        excerpt=obs.excerpt,
        provenance=[
            EvidenceProvenance(
                schema_version=schema_version,
                retrieval_id=accepted.retrieval_id,
                method=accepted.method,
            )
        ],
        confidence=obs.confidence,
        limitations=[*obs.limitations, *extra_limitations],
        conflicts_with=[],
        **core,
    )


def _stage_info(
    items: list[_Accepted],
    made: list[Evidence],
    valid: list[Evidence],
    resolution: str,
    *,
    schema_version: str,
) -> StageInfo:
    if not items or resolution == "conflict":
        rationale = (
            "단계 근거 없음"
            if not items
            else "단계 근거가 상충하고 사건일로 해소되지 않음"
        )
        return StageInfo(
            schema_version=schema_version,
            normalized_round="unknown",
            bucket="unknown",
            method="unknown",
            source_ids=sorted({a.observation.source_id for a in items}),
            confidence="unknown",
            rationale=rationale,
        )
    valid_ids = {e.evidence_id for e in valid}
    current = [
        a.observation
        for a, ev in zip(items, made, strict=True)
        if ev.evidence_id in valid_ids
    ]
    # 같은 round면 explicit 관측을 대표로 둔다. 추정 근거는 판정에서 무효다(D06).
    explicit = [o for o in current if o.value.method == "explicit"]
    winner = (explicit or current)[-1]
    stage = winner.value
    assert isinstance(stage, StageObservation)
    return StageInfo(
        schema_version=schema_version,
        raw_label=stage.raw_label,
        normalized_round=stage.normalized_round,
        bucket="unknown",
        last_round_date=winner.event_date,
        method=stage.method,
        source_ids=sorted({e.source_id for e in valid}),
        confidence=winner.confidence,
        rationale=(
            "최신 사건일 근거로 이전 단계 근거를 대체"
            if resolution == "supersede"
            else "단계 근거 일치"
        ),
    )


def assemble_bundle(
    candidate: Candidate,
    sources: Sequence[Source],
    observations: Sequence[tuple[FieldObservation, str, Literal["web", "api"]]],
    *,
    as_of: date,
    schema_version: str,
) -> tuple[CompanyResearchBundle, list[Rejection], dict[str, str]]:
    """관측을 Evidence·profile로. ``observations``는 (관측, retrieval_id, method).

    반환: bundle, 버린 관측, 기준일로 제외한 Source ID → 사유.
    """
    by_id = {s.source_id: s for s in sources}
    excluded: dict[str, str] = {}
    for source in by_id.values():
        decision = check_as_of(source, as_of)
        if not decision.admitted:
            excluded[source.source_id] = decision.reason.value

    rejected: list[Rejection] = []
    per_field: dict[str, list[_Accepted]] = {f: [] for f in RESEARCH_FIELDS}
    for obs, retrieval_id, method in observations:
        source = by_id.get(obs.source_id)
        if source is None:
            raise ValueError(f"observation refers to unknown Source {obs.source_id}")
        reason = excluded.get(obs.source_id)
        if reason is None and obs.event_date is not None and obs.event_date > as_of:
            reason = "EVENT_AFTER_AS_OF"
        reason = reason or _identity_rejection(obs, candidate, source)
        if reason:
            rejected.append(Rejection(obs.field, obs.source_id, reason))
            continue
        per_field[obs.field].append(_Accepted(obs, retrieval_id, method))

    evidence: dict[str, Evidence] = {}
    field_ids: dict[str, list[str]] = {}
    values: dict[str, bool | None] = {}
    stage: StageInfo | None = None
    for name in sorted(RESEARCH_FIELDS):
        items = per_field[name]
        resolution, ordered = _resolve(items)
        made: list[Evidence] = []
        if resolution == "supersede":
            previous = None
            for item in ordered:
                note = [] if previous is None else ["이전 사건일 근거를 대체"]
                ev = _evidence(
                    candidate.candidate_id,
                    item,
                    schema_version=schema_version,
                    supersedes=previous,
                    extra_limitations=note,
                )
                made.append(ev)
                # 최신 사건일 값과 같은 이전 근거까지 모두 대체해 최신 근거만 남긴다.
                if item.observation.event_date != ordered[-1].observation.event_date:
                    previous = ev.evidence_id
            superseded = {e.supersedes for e in made if e.supersedes}
            valid = [e for e in made if e.evidence_id not in superseded]
        else:
            made = [
                _evidence(
                    candidate.candidate_id,
                    item,
                    schema_version=schema_version,
                    supersedes=None,
                )
                for item in ordered
            ]
            if resolution == "conflict":
                ids = [e.evidence_id for e in made]
                for i, ev in enumerate(made):
                    rivals = [
                        other
                        for j, other in enumerate(ids)
                        if _value_key(ordered[j].observation)
                        != _value_key(ordered[i].observation)
                    ]
                    made[i] = ev.model_copy(
                        update={
                            "conflicts_with": sorted(rivals),
                            "limitations": [*ev.limitations, "다른 출처와 상충"],
                        }
                    )
                valid = []
            else:
                valid = made
        for ev in made:
            evidence[ev.evidence_id] = ev
        if made:
            field_ids[name] = sorted({e.evidence_id for e in made})
        if name in BOOLEAN_FIELDS:
            values[name] = (
                None
                if resolution == "conflict" or not items
                else ordered[-1].observation.value
            )
        if name == FIELD_STAGE:
            stage = _stage_info(
                ordered, made, valid, resolution, schema_version=schema_version
            )

    admitted = {sid: s for sid, s in by_id.items() if sid not in excluded}
    profile = CompanyProfile(
        schema_version=schema_version,
        candidate_id=candidate.candidate_id,
        domain_match=values[FIELD_DOMAIN],
        is_listed=values[FIELD_LISTING],
        exit_completed=values[FIELD_EXIT],
        stage=stage,
        as_of=as_of,
        field_evidence_ids=field_ids,
    )
    bundle = CompanyResearchBundle(
        schema_version=schema_version,
        sources=admitted,
        evidence=evidence,
        profile=profile,
    )
    return bundle, rejected, excluded


# ---------------------------------------------------------------- composer


class LiveResearchCompany:
    """``ResearchCompany``. provider를 순서대로 실행하고 bundle을 조립한다.

    ``as_of``는 실행 기준일(RunInput)이다. provider 목록·fetch 정책·예산은 호출자가
    승인된 설정에서 주입하며 이 클래스에 기본값이 없다.
    """

    def __init__(
        self,
        providers: Sequence[CompanyResearchProvider],
        *,
        run_id: str,
        schema_version: str,
        as_of: date,
        clock: Clock,
        retrieval_namespace: str | None = None,
    ) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        names = [p.name for p in providers]
        if len(set(names)) != len(names):
            raise ValueError("provider names must be unique")
        self._providers = list(providers)
        self._run_id = run_id
        self._schema_version = schema_version
        self._as_of = as_of
        self._clock = clock
        self._retrieval_namespace = retrieval_namespace
        self.calls = 0

    def __call__(
        self, candidate: Candidate, budget: ToolBudget
    ) -> ToolResult[CompanyResearchBundle]:
        self.calls += 1
        started_at = self._clock.now()
        prefix = (
            f"retrieval-{TOOL_NAME}-{self._run_id}-"
            f"{candidate.candidate_id}-{self.calls}"
        )
        if self._retrieval_namespace is not None:
            prefix += f"-{self._retrieval_namespace}"
        if budget.max_calls < 1:
            return self._failure(
                candidate,
                started_at,
                prefix,
                [],
                ErrorCode.BUDGET_EXHAUSTED,
                "company research budget exhausted before any request",
            )

        calls = CallBudget(
            budget.max_calls, clock=self._clock, deadline=budget.deadline
        )
        records: list[RetrievalRecord] = []
        sources: list[Source] = []
        observations: list[tuple[FieldObservation, str, Literal["web", "api"]]] = []
        summary: dict[str, JSONMap] = {}
        for provider in self._providers:
            outcome = provider(candidate, calls)
            source_record: dict[str, tuple[str, Literal["web", "api"]]] = {}
            for call in outcome.calls:
                rid = f"{prefix}-{len(records) + 1}"
                records.append(self._call_record(candidate, provider.name, rid, call))
                for sid in call.source_ids:
                    source_record[sid] = (rid, call.method)
            summary[provider.name] = {
                "status": outcome.status,
                "required": provider.required,
                "error_code": outcome.error_code.value if outcome.error_code else None,
                "skipped": outcome.skipped,
                "notes": list(outcome.notes),
            }
            if outcome.status in ("ok", "empty") or provider.required:
                sources.extend(outcome.sources)
            if outcome.error_code is not None and provider.required:
                return self._failure(
                    candidate,
                    started_at,
                    prefix,
                    records,
                    outcome.error_code,
                    f"required provider {provider.name}: {outcome.message}",
                    summary,
                    sources=sources,
                    requests_used=calls.used,
                    extractor_error_code=outcome.extractor_error_code,
                )
            if outcome.status not in ("ok", "empty"):
                continue
            for obs in outcome.observations:
                if obs.source_id not in source_record:
                    raise ValueError(
                        f"{provider.name}: observation Source without call"
                    )
                rid, method = source_record[obs.source_id]
                observations.append((obs, rid, method))

        bundle, rejected, excluded = assemble_bundle(
            candidate,
            sources,
            observations,
            as_of=self._as_of,
            schema_version=self._schema_version,
        )
        status: ToolStatus = "ok" if bundle.sources or bundle.evidence else "empty"
        summary_args: JSONMap = {
            "as_of": self._as_of.isoformat(),
            "providers": summary,
            "requests_used": calls.used,
            "rejected_observations": [
                {"field": r.field, "source_id": r.source_id, "reason": r.reason}
                for r in rejected
            ],
            "excluded_sources": excluded,
        }
        records.append(
            self._summary_record(
                candidate,
                started_at,
                prefix,
                status,
                summary_args,
                source_ids=sorted(bundle.sources),
                evidence_ids=sorted(bundle.evidence),
                error_id=None,
            )
        )
        return ToolResult[CompanyResearchBundle](
            schema_version=self._schema_version,
            status=status,
            data=bundle,
            retrieval_records=records,
            errors=[],
        )

    def _call_record(
        self, candidate: Candidate, provider: str, rid: str, call: ProviderCall
    ) -> RetrievalRecord:
        arguments: JSONMap = {"provider": provider, **call.arguments}
        if call.error_code is not None:
            arguments["error_code"] = call.error_code.value
        return RetrievalRecord(
            schema_version=self._schema_version,
            retrieval_id=rid,
            run_id=self._run_id,
            candidate_id=candidate.candidate_id,
            tool_name=f"{TOOL_NAME}/{provider}",
            query=call.query,
            arguments_without_secrets=arguments,
            started_at=call.started_at,
            finished_at=call.finished_at,
            status=call.status,
            source_ids=list(call.source_ids),
            chunk_ids=[],
            evidence_ids=[],
            error_id=None,
            cost=None,
            cache_hit=False,
        )

    def _summary_record(
        self,
        candidate: Candidate,
        started_at: datetime,
        prefix: str,
        status: ToolStatus,
        arguments: JSONMap,
        *,
        source_ids: list[str],
        evidence_ids: list[str],
        error_id: str | None,
    ) -> RetrievalRecord:
        return RetrievalRecord(
            schema_version=self._schema_version,
            retrieval_id=prefix,
            run_id=self._run_id,
            candidate_id=candidate.candidate_id,
            tool_name=TOOL_NAME,
            query=candidate.canonical_name,
            arguments_without_secrets=arguments,
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
        self,
        candidate: Candidate,
        started_at: datetime,
        prefix: str,
        records: list[RetrievalRecord],
        code: ErrorCode,
        message: str,
        summary: Mapping[str, JSONMap] | None = None,
        *,
        sources: Sequence[Source] = (),
        requests_used: int = 0,
        extractor_error_code: ErrorCode | None = None,
    ) -> ToolResult[CompanyResearchBundle]:
        spec = ERROR_SPECS[code]
        error = WorkflowError(
            schema_version=self._schema_version,
            error_id=f"error-{prefix}",
            run_id=self._run_id,
            candidate_id=candidate.candidate_id,
            node=NODE,
            error_code=code.value,
            message_redacted=message,
            retryable=spec.retryable,
            attempt=1,
            timestamp=started_at,
        )
        retained_sources = {
            source.source_id: source.model_dump(mode="json")
            for source in sources
            if check_as_of(source, self._as_of).admitted
        }
        arguments: JSONMap = {
            "as_of": self._as_of.isoformat(),
            "providers": dict(summary or {}),
            "requests_used": requests_used,
            "retained_sources": retained_sources,
        }
        if extractor_error_code is not None:
            original = error.model_copy(
                update={
                    "error_code": extractor_error_code.value,
                    "retryable": ERROR_SPECS[extractor_error_code].retryable,
                    "message_redacted": (
                        "required official homepage eligibility extraction failed"
                    ),
                }
            )
            arguments["extractor_error"] = original.model_dump(mode="json")
        summary_record = self._summary_record(
            candidate,
            started_at,
            prefix,
            spec.tool_status,
            arguments,
            source_ids=sorted(retained_sources),
            evidence_ids=[],
            error_id=error.error_id,
        )
        return ToolResult[CompanyResearchBundle](
            schema_version=self._schema_version,
            status=spec.tool_status,
            data=None,
            retrieval_records=[*records, summary_record],
            errors=[error],
        )
