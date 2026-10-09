"""출처 구간에서 검증 가능한 주장·수치를 뽑는 versioned prompt — #50, T05·T18.

출처 원문은 **untrusted data**다. system prompt에는 지시만 두고, 원문은 user
payload의 JSON 문자열 필드(``untrusted_source_text``)에만 넣는다. JSON escape
때문에 원문 안의 따옴표·구분자·가짜 지시가 payload 구조를 벗어나지 못한다.
prompt에는 출처 구간·후보 이름·기준일만 넣고 환경 변수·API key·도구 목록은 넣지 않는다.

출력 schema(``ExtractionOutput``)에는 ID·출처·provenance·도구 호출 필드가 없다.
모델 출력은 모두 후보값이며 ``agents.evidence_extraction``이 원문과 대조해 검증한다.
"""

import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from skala_rag.contracts.common import ISODate, Number
from skala_rag.prompt._resources import read_prompt
from skala_rag.prompt.versions import EVIDENCE_EXTRACTION_VERSION

PROMPT_VERSION = EVIDENCE_EXTRACTION_VERSION

SYSTEM_PROMPT = read_prompt("evidence_extraction.json")


class ClaimDraft(BaseModel):
    """모델이 제안한 주장 하나. 코드 검증 전에는 Evidence가 아니다."""

    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    subject: str | None = None
    value: Number | None = None
    unit: str | None = None
    currency: str | None = None
    value_as_of: ISODate | None = None
    period: str | None = None
    geography: str | None = None
    event_date: ISODate | None = None
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"
    limitations: list[str] = []


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ClaimDraft]


def build_user_prompt(
    *,
    source_text: str,
    scope: Literal["company", "industry"],
    target_names: list[str],
    as_of: date,
    criterion_ids: list[str],
) -> str:
    """결정적 JSON payload. 원문은 ``untrusted_source_text`` 문자열로만 들어간다."""
    payload = {
        "prompt_version": PROMPT_VERSION,
        "task": "extract_verifiable_claims",
        "scope": scope,
        "target_company_names": target_names,
        "as_of": as_of.isoformat(),
        "criterion_ids": criterion_ids,
        "untrusted_source_text": source_text,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


__all__ = [
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "ClaimDraft",
    "ExtractionOutput",
    "build_user_prompt",
]
