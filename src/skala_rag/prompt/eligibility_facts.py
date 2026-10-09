"""공식 원문에서 적격성 사실 후보를 뽑는 versioned prompt — #51, T04.

출처 원문은 **untrusted data**다(#50 ``evidence_extraction`` prompt와 같은 원칙).
system prompt에는 지시만 두고, 원문은 user payload의 JSON 문자열 필드
(``untrusted_source_text``)에만 넣는다. 도메인 정의는 호출자가 승인된 설정에서 넘긴다.

모델은 단계 표기를 원문 그대로(``stage_label``) 옮기기만 한다. Seed~C 정규화는
``agents.eligibility_extraction``이 D06 규칙으로 결정적으로 한다. 모델 출력은 모두
후보값이며 코드가 원문과 대조해 검증한다.
"""

import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from skala_rag.contracts.common import ISODate
from skala_rag.prompt._resources import read_prompt
from skala_rag.prompt.versions import ELIGIBILITY_FACTS_VERSION

PROMPT_VERSION = ELIGIBILITY_FACTS_VERSION

SYSTEM_PROMPT = read_prompt("eligibility_facts.json")

FactField = Literal["domain_match", "business", "is_listed", "exit_completed", "stage"]


class FactDraft(BaseModel):
    """모델이 제안한 적격성 사실 하나. 코드 검증 전에는 관측이 아니다."""

    model_config = ConfigDict(extra="forbid")

    field: FactField
    value: bool | None = None
    stage_label: str | None = None
    event_date: ISODate | None = None
    subject: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"


class EligibilityFactsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts: list[FactDraft]


def build_user_prompt(
    *,
    source_text: str,
    target_names: list[str],
    domain_definition: str,
    as_of: date,
) -> str:
    """결정적 JSON payload. 원문은 ``untrusted_source_text`` 문자열로만 들어간다."""
    payload = {
        "prompt_version": PROMPT_VERSION,
        "task": "extract_eligibility_facts",
        "target_company_names": target_names,
        "domain_definition": domain_definition,
        "as_of": as_of.isoformat(),
        "untrusted_source_text": source_text,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


__all__ = [
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "FactField",
    "FactDraft",
    "EligibilityFactsOutput",
    "build_user_prompt",
]
