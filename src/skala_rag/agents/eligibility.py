"""적격성 판정 — scoring.md §1, D06(APPROVED). 점수보다 먼저 적용한다.

결정적 규칙만 쓴다. LLM·외부 호출이 없다.

- 조건: 도메인, 상장, 투자 단계, Exit, 최소 평가 가능성.
- 조건마다 ``pass``/``fail``/``unknown``. 하나라도 fail이면 ineligible, 그 외
  하나라도 unknown이면 unknown, 모두 pass일 때만 eligible.
- 값(true/false/round)은 ``profile.field_evidence_ids``의 해당 키에 유효한 근거가
  있을 때만 인정한다. 근거 없는 false는 unknown이다(검색 0건 ≠ 비상장, 모름 ≠
  Exit 없음).
- 유효 근거: 같은 후보의 company 근거, ``estimated`` 아님, 다른 근거가 대체
  (supersedes)하지 않음, 전달된 근거와 상충(conflicts_with)하지 않음.
- 단계: 명시적(explicit)으로 확인된 Seed~C만 pass. TIPS 표기만으로 seed를
  확정하지 않는다. 명시적 out_of_scope(프리시드·엔젤·Series D 이상)는 fail.
  추정·unknown은 unknown.

``field_evidence_ids`` 키는 아래 ``FIELD_*`` 상수다. 이 키 이름은 구현 제안이며
ResearchGap.eligibility_field catalog를 정하지 않는다.
"""

import re
from collections.abc import Mapping
from typing import Literal

from skala_rag.contracts.candidates import CompanyProfile, EligibilityResult
from skala_rag.contracts.common import JSONMap
from skala_rag.contracts.evidence import Evidence
from skala_rag.contracts.ids import eligibility_result_id

CheckStatus = Literal["pass", "fail", "unknown"]

FIELD_DOMAIN = "domain_match"
FIELD_LISTING = "is_listed"
FIELD_STAGE = "stage"
FIELD_EXIT = "exit_completed"
FIELD_IDENTITY = "identity"
FIELD_BUSINESS = "business"

IN_SCOPE_ROUNDS = frozenset({"seed", "series_a", "series_b", "series_c"})
# 원문 정규화 표의 검색 힌트. 이 표기만으로 Seed~C를 확정하지 않는다(D06).
_TIPS = re.compile(r"tips", re.IGNORECASE)
_PRE_SEED = re.compile(r"pre[\s_-]?seed|프리\s*시드|angel|엔젤", re.IGNORECASE)

ELIGIBILITY_CONFIRMED = "ELIGIBILITY_CONFIRMED"


class EligibilityInputError(ValueError):
    """호출자 배선 오류: 없는 근거 참조, 다른 후보 근거, 잘못된 policy."""


def _valid_evidence(
    candidate_id: str,
    field: str,
    profile: CompanyProfile,
    evidence: Mapping[str, Evidence],
    superseded: set[str],
) -> tuple[list[str], str | None]:
    """해당 필드의 유효 근거 ID와, 전부 무효일 때 그 이유 코드."""
    ids = profile.field_evidence_ids.get(field, [])
    valid: list[str] = []
    reason = None
    for evidence_id in ids:
        item = evidence.get(evidence_id)
        if item is None:
            raise EligibilityInputError(f"{field}: 전달되지 않은 근거 참조")
        if item.candidate_id != candidate_id or item.scope != "company":
            raise EligibilityInputError(f"{field}: 다른 후보 또는 산업 근거")
        if evidence_id in superseded:
            reason = reason or "SUPERSEDED"
        elif item.evidence_kind == "estimated":
            reason = reason or "ESTIMATED"
        elif any(other in evidence for other in item.conflicts_with):
            reason = "CONFLICT"
        else:
            valid.append(evidence_id)
    return sorted(set(valid)), reason


def _check(
    status: CheckStatus, reason_code: str, evidence_ids: list[str], **extra
) -> dict:
    return {
        "status": status,
        "reason_code": reason_code,
        "evidence_ids": evidence_ids,
        **extra,
    }


def _boolean_check(
    prefix: str,
    value: bool | None,
    ids: list[str],
    invalid: str | None,
    *,
    pass_when: bool,
    pass_code: str,
    fail_code: str,
    **extra,
) -> dict:
    if value is None:
        return _check("unknown", f"{prefix}_UNKNOWN", ids, value=None, **extra)
    if not ids:
        # 값은 있으나 인정할 근거가 없다: 모름을 true/false로 바꾸지 않는다.
        code = f"{prefix}_{invalid}" if invalid else f"{prefix}_UNVERIFIED"
        return _check("unknown", code, ids, value=value, **extra)
    if value is pass_when:
        return _check("pass", pass_code, ids, value=value, **extra)
    return _check("fail", fail_code, ids, value=value, **extra)


def _stage_check(profile: CompanyProfile, ids: list[str], invalid: str | None):
    stage = profile.stage
    extra = {
        "normalized_round": stage.normalized_round,
        "method": stage.method,
        "raw_label": stage.raw_label,
    }
    label = stage.raw_label or ""
    if stage.method != "explicit" or stage.normalized_round == "unknown":
        code = "STAGE_ESTIMATED" if stage.method == "estimated" else "STAGE_UNKNOWN"
        return _check("unknown", code, ids, **extra)
    if not ids:
        code = f"STAGE_{invalid}" if invalid else "STAGE_UNVERIFIED"
        return _check("unknown", code, ids, **extra)
    if stage.normalized_round == "out_of_scope":
        return _check("fail", "STAGE_OUT_OF_SCOPE", ids, **extra)
    if _TIPS.search(label):
        return _check("unknown", "STAGE_TIPS_ONLY", ids, **extra)
    if _PRE_SEED.search(label):
        # 프리시드·엔젤 표기를 Seed~C로 올리지 않는다. 명시적이면 out_of_scope여야 한다.
        return _check("unknown", "STAGE_LABEL_MISMATCH", ids, **extra)
    return _check("pass", "STAGE_IN_SCOPE", ids, **extra)


def _evaluability_check(identity, business) -> dict:
    ids = sorted(set(identity[0]) | set(business[0]))
    if not identity[0]:
        return _check("unknown", "IDENTITY_UNVERIFIED", ids)
    if not business[0]:
        return _check("unknown", "BUSINESS_EVIDENCE_MISSING", ids)
    return _check("pass", "EVALUABLE", ids)


def check_eligibility(
    profile: CompanyProfile,
    evidence: Mapping[str, Evidence],
    policy: JSONMap,
    *,
    run_id: str,
    evidence_revision: int,
) -> EligibilityResult:
    """contracts §7 ``check_eligibility``. ``policy``에는 ``policy_version``이 필요하다.

    ``run_id``·``evidence_revision``은 결과 ID(contracts §4)에 필요해 키워드로 받는다.
    """
    policy_version = policy.get("policy_version")
    if not isinstance(policy_version, str) or not policy_version.strip():
        raise EligibilityInputError("policy_version이 필요하다")
    if any(key != item.evidence_id for key, item in evidence.items()):
        raise EligibilityInputError("Evidence map key 불일치")

    candidate_id = profile.candidate_id
    superseded = {item.supersedes for item in evidence.values() if item.supersedes}

    def field(name: str):
        return _valid_evidence(candidate_id, name, profile, evidence, superseded)

    domain, listing, stage, exit_ = (
        field(FIELD_DOMAIN),
        field(FIELD_LISTING),
        field(FIELD_STAGE),
        field(FIELD_EXIT),
    )
    checks = {
        "domain": _boolean_check(
            "DOMAIN",
            profile.domain_match,
            *domain,
            pass_when=True,
            pass_code="DOMAIN_MATCH",
            fail_code="DOMAIN_MISMATCH",
        ),
        "listing": _boolean_check(
            "LISTING",
            profile.is_listed,
            *listing,
            pass_when=False,
            pass_code="UNLISTED",
            fail_code="LISTED",
        ),
        "stage": _stage_check(profile, *stage),
        "exit": _boolean_check(
            "EXIT",
            profile.exit_completed,
            *exit_,
            pass_when=False,
            pass_code="NO_EXIT",
            fail_code="EXIT_COMPLETED",
            # 자료 범위: 이 기준일까지 전달된 근거로만 확인했다.
            as_of=profile.as_of.isoformat(),
        ),
        "evaluability": _evaluability_check(
            field(FIELD_IDENTITY), field(FIELD_BUSINESS)
        ),
    }

    statuses = [check["status"] for check in checks.values()]
    if "fail" in statuses:
        status = "ineligible"
    elif "unknown" in statuses:
        status = "unknown"
    else:
        status = "eligible"
    reason_codes = [c["reason_code"] for c in checks.values() if c["status"] == "fail"]
    reason_codes += [
        c["reason_code"] for c in checks.values() if c["status"] == "unknown"
    ]
    evidence_ids = sorted({i for c in checks.values() for i in c["evidence_ids"]})

    return EligibilityResult(
        schema_version=profile.schema_version,
        eligibility_result_id=eligibility_result_id(
            run_id, candidate_id, evidence_revision, policy_version
        ),
        run_id=run_id,
        candidate_id=candidate_id,
        evidence_revision=evidence_revision,
        policy_version=policy_version,
        as_of=profile.as_of,
        status=status,
        checks=checks,
        reason_codes=reason_codes or [ELIGIBILITY_CONFIRMED],
        evidence_ids=evidence_ids,
    )
