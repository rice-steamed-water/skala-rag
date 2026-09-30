"""명시적 근거 충분성 검사와 draft catalog로 계산하는 fixture Coverage."""

from collections.abc import Callable, Sequence
from typing import Literal

from skala_rag.contracts import CoverageResult, Evidence, ResearchGap
from skala_rag.scoring.catalog import Criterion, ScoringPolicy

SupportCheck = Callable[[Criterion, tuple[Evidence, ...]], bool]


def check_coverage(
    candidate_id: str,
    evidence: Sequence[Evidence],
    catalog: ScoringPolicy,
    *,
    evidence_revision: int,
    schema_version: str,
    execution_mode: Literal["fixture", "live"],
    support_check: SupportCheck,
    unresolved_conflict_ids: Sequence[str],
) -> CoverageResult:
    """지원 근거·필수 맥락을 확인한 criterion의 비중만 합산한다.

    support_check는 단위·기간·주체·산업 관련성 등을 검증하는 주입 함수다.
    criterion_ids만으로 관측을 인정하지 않는다. as_of/허용 출처/정정 근거
    필터링은 수집 경계 책임이다. 미해결 상충 근거는 검사 입력에서 제외한다.
    """
    if execution_mode != "fixture":
        raise ValueError("draft Coverage는 fixture 모드에서만 사용할 수 있습니다")
    known_ids = {criterion.criterion_id for criterion in catalog.criteria}
    evidence_ids = [item.evidence_id for item in evidence]
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("중복 Evidence ID는 수집 경계에서 병합해야 합니다")
    conflicts = set(unresolved_conflict_ids)
    if not conflicts <= set(evidence_ids):
        raise ValueError("미해결 conflict ID가 전달된 Evidence에 없습니다")
    relevant = []
    for item in evidence:
        if not set(item.criterion_ids) <= known_ids:
            raise ValueError("catalog에 없는 criterion ID가 있습니다")
        if item.scope == "company" and item.candidate_id != candidate_id:
            continue
        if item.scope == "industry" and item.candidate_id is not None:
            raise ValueError("industry 근거의 candidate_id는 None이어야 합니다")
        relevant.append(item)
    blocked = {
        item.evidence_id
        for item in relevant
        if item.evidence_id in conflicts or set(item.conflicts_with) & conflicts
    }
    covered, missing = [], []
    missing_weight = 0
    for criterion in catalog.criteria:
        matches = tuple(
            item for item in relevant if criterion.criterion_id in item.criterion_ids
        )
        # 관련 미해결 상충이 있으면 일부 근거만 골라 관측을 승인하지 않는다.
        usable = (
            matches if not any(item.evidence_id in blocked for item in matches) else ()
        )
        supported = support_check(criterion, usable) if usable else False
        if type(supported) is not bool:
            raise TypeError("support_check는 bool을 반환해야 합니다")
        if supported:
            covered.append(criterion.criterion_id)
        else:
            missing.append(criterion.criterion_id)
            missing_weight += criterion.weight
    return CoverageResult(
        schema_version=schema_version,
        candidate_id=candidate_id,
        evidence_revision=evidence_revision,
        policy_version=catalog.policy_version,
        covered_criterion_ids=covered,
        missing_criterion_ids=missing,
        missing_weight=missing_weight,
        coverage_pct=100 - missing_weight,
        research_ready=missing_weight < catalog.thresholds.missing_weight,
        unresolved_conflicts=[
            item.evidence_id for item in relevant if item.evidence_id in blocked
        ],
    )


def build_research_gaps(
    coverage: CoverageResult,
    catalog: ScoringPolicy,
    templates: Sequence[ResearchGap],
) -> list[ResearchGap]:
    """호출자가 명시한 ID·질문·결측 사유를 비중 내림차순 gap으로 만든다.

    쿼리나 gap ID를 임의 생성하지 않는다. 모든 missing criterion의 템플릿을
    정확히 한 번 요구하고 비중은 모델/템플릿 출력 대신 catalog로 계산한다.
    """
    if coverage.policy_version != catalog.policy_version:
        raise ValueError("Coverage와 catalog 정책 버전이 다릅니다")
    weights = {item.criterion_id: item.weight for item in catalog.criteria}
    targets = [item.criterion_id for item in templates]
    if len(set(targets)) != len(targets) or set(targets) != set(
        coverage.missing_criterion_ids
    ):
        raise ValueError("모든 missing criterion의 gap이 정확히 한 번 필요합니다")
    if len({item.gap_id for item in templates}) != len(templates):
        raise ValueError("gap ID가 중복되었습니다")
    gaps = []
    for template in templates:
        if template.candidate_id != coverage.candidate_id or template.status != "open":
            raise ValueError("같은 후보의 open gap만 생성할 수 있습니다")
        if not template.suggested_queries or not template.missing_fields:
            raise ValueError("gap에는 검색 질문과 결측 필드가 필요합니다")
        payload = template.model_dump(mode="json")
        payload["priority_weight"] = weights[template.criterion_id]
        gaps.append(ResearchGap.model_validate(payload))
    # 동률은 catalog 순서를 따라 실행마다 같은 순서를 유지한다.
    order = {criterion.criterion_id: i for i, criterion in enumerate(catalog.criteria)}
    return sorted(gaps, key=lambda gap: (-gap.priority_weight, order[gap.criterion_id]))
