"""Technology 영역 structured evaluation용 versioned prompt — #57, T01·T13.

근거 원문(claim·excerpt·limitations)은 **untrusted data**다. system prompt에는
지시만 두고, 원문은 user payload의 ``untrusted_source_text`` JSON 문자열 필드에만
넣는다. JSON escape 때문에 원문 속 따옴표·구분자·가짜 지시가 payload 구조를
벗어나지 못한다. prompt에는 rubric·criterion·snapshot 근거만 넣고 환경 변수·
API key·도구 목록·검색 기능은 넣지 않는다(노드 안 검색 금지).

출력 schema는 공통 ``DimensionAssessmentOutput``(#22)을 그대로 쓴다. 점수·비중·
provenance는 모델이 내지 않고 코드가 계산·기록한다.
"""

import json
from collections.abc import Mapping

from skala_rag.contracts.evaluation import EvaluationSnapshot
from skala_rag.prompt._resources import read_prompt
from skala_rag.prompt.versions import TECHNOLOGY_EVALUATION_VERSION
from skala_rag.scoring.approved_policy import ApprovedScoringPolicy
from skala_rag.scoring.catalog import ScoringPolicy

PROMPT_VERSION = TECHNOLOGY_EVALUATION_VERSION

DIMENSION = "technology"

SYSTEM_PROMPT = read_prompt("technology_evaluation.json")


def _criteria(policy: ScoringPolicy | ApprovedScoringPolicy) -> list[str]:
    return sorted(c.criterion_id for c in policy.criteria if c.dimension == DIMENSION)


def build_user_prompt(
    snapshot: EvaluationSnapshot,
    rubric: Mapping[str, object],
    policy: ScoringPolicy | ApprovedScoringPolicy,
) -> str:
    """결정적 JSON payload. 근거 원문은 ``untrusted_source_text``로만 들어간다."""
    dims = rubric.get("dimensions")
    rubric_dim = dims.get(DIMENSION, {}) if isinstance(dims, Mapping) else {}
    payload = {
        "prompt_version": PROMPT_VERSION,
        "dimension": DIMENSION,
        "as_of": snapshot.as_of,
        "rubric_version": rubric.get("rubric_version"),
        "criteria": _criteria(policy),
        "rubric": rubric_dim,
        "evidence": [
            {
                "evidence_id": e.evidence_id,
                "criterion_ids": e.criterion_ids,
                "evidence_kind": e.evidence_kind,
                "confidence": e.confidence,
                "value": e.value,
                "unit": e.unit,
                "period": e.period,
                "event_date": e.event_date,
                "untrusted_source_text": {
                    "claim": e.claim,
                    "excerpt": e.excerpt,
                    "limitations": e.limitations,
                },
            }
            for e in sorted(snapshot.evidence.values(), key=lambda x: x.evidence_id)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


__all__ = [
    "PROMPT_VERSION",
    "DIMENSION",
    "SYSTEM_PROMPT",
    "build_user_prompt",
]
