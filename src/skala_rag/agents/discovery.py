"""Discovery 결과 검증과 Candidate Normalize (architecture §3).

- ``accept_discovery``: ``search_candidates`` 결과를 발견/0건/실패로 구별하고
  모든 ``discovery_source_ids``가 bundle ``sources``로 해소되는지 확인한다.
- ``normalize_candidates``: 동일 법인 중복을 합치고, 이름만 같은 기업은 합치지
  않는다. 병합마다 근거를 남긴다. 중복 제거 후 ``max_candidates``를 넘으면
  주입한 ``CandidateLimitPolicy``로만 고른다. 어떤 후보를 남길지는 D08 OPEN
  (#35)이라 기본 정책이 없고, 정책 없이 초과하면 ``CandidateLimitUnresolved``.
- ``discovery_state_update``: 발견 Source·검색 이력·오류를 State patch로 만든다.
  이후 Company Research가 실패해도 이 값은 다른 노드가 지우지 않는다.

동일 법인 판단 (이름·별칭은 근거로 쓰지 않는다):

1. 국가가 다르거나, 같은 식별체계의 법인 식별자 값이 다르면 다른 법인이다.
2. 그렇지 않을 때 candidate_id, 법인 식별자(체계·값), 홈페이지 host 중 하나라도
   같으면 같은 법인이다.
3. 어느 것도 같지 않으면 이름이 같아도 별도 후보로 남긴다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from skala_rag.contracts.bundles import DiscoveryBundle
from skala_rag.contracts.candidates import Candidate
from skala_rag.contracts.errors import WorkflowError
from skala_rag.contracts.retrieval import RetrievalRecord
from skala_rag.contracts.state import CandidateStatus, InvestmentState
from skala_rag.contracts.tools import ToolResult


class DiscoveryInvalid(ValueError):
    """status와 data가 어긋나거나 발견 출처가 해소되지 않는 도구 결과."""


@dataclass(frozen=True)
class DiscoveryOutcome:
    """Discovery 한 번의 결과. 0건(no_candidates)과 도구 실패(failed)를 구별한다."""

    status: Literal["found", "no_candidates", "failed"]
    bundle: DiscoveryBundle | None
    retrieval_records: list[RetrievalRecord]
    errors: list[WorkflowError]


def accept_discovery(result: ToolResult[DiscoveryBundle]) -> DiscoveryOutcome:
    records, errors = list(result.retrieval_records), list(result.errors)
    if result.status in ("unavailable", "failed"):
        return DiscoveryOutcome("failed", None, records, errors)

    bundle = result.data
    if result.status == "empty":
        if bundle.candidates:
            raise DiscoveryInvalid("empty discovery result has candidates")
        return DiscoveryOutcome("no_candidates", bundle, records, errors)
    if not bundle.candidates:
        raise DiscoveryInvalid("ok discovery result has no candidates")
    for candidate in bundle.candidates:
        if not candidate.discovery_source_ids:
            raise DiscoveryInvalid(
                f"candidate {candidate.candidate_id} has no discovery Source"
            )
        missing = [s for s in candidate.discovery_source_ids if s not in bundle.sources]
        if missing:
            raise DiscoveryInvalid(
                f"candidate {candidate.candidate_id} has unresolved Source {missing}"
            )
    return DiscoveryOutcome("found", bundle, records, errors)


@dataclass(frozen=True)
class CandidateMerge:
    """병합 근거. ``matched_on`` 예: ``legal_identifier:brn``, ``homepage_host``."""

    kept_candidate_id: str
    merged_candidate_id: str
    matched_on: list[str]


@dataclass(frozen=True)
class NormalizeResult:
    """Candidate는 이미 검증된 DTO라 다시 검증하지 않는다(fixture context 불필요)."""

    candidates: list[Candidate]
    merges: list[CandidateMerge]
    dropped_candidate_ids: list[str]
    """중복 제거 후 상한 정책이 제외한 후보. 발견 순서를 따른다."""


class CandidateLimitUnresolved(ValueError):
    """후보가 상한을 넘었는데 승인된 선택 정책이 주입되지 않았다."""


@runtime_checkable
class CandidateLimitPolicy(Protocol):
    """상한 초과 시 남길 후보를 고르는 승인 정책(D08, 미결정).

    중복 제거된 후보(발견 순서)와 상한을 받아, 남길 candidate_id를 처리 순서대로
    돌려준다. 결과는 중복 없는 입력 부분집합이며 1개 이상 ``max_candidates`` 이하.
    """

    def __call__(
        self, candidates: Sequence[Candidate], max_candidates: int
    ) -> Sequence[str]: ...


def _country(candidate: Candidate) -> str:
    return candidate.country.strip().upper()


def _host(url: str | None) -> str | None:
    if url is None:
        return None
    host = urlsplit(url.strip()).hostname
    if not host:
        return None
    host = host.lower().rstrip(".")
    return host.removeprefix("www.")


def _identifiers(candidate: Candidate) -> dict[str, str]:
    return {
        scheme.strip().lower(): value.strip()
        for scheme, value in candidate.legal_identifiers.items()
    }


@dataclass
class _Cluster:
    candidate: Candidate
    member_ids: set[str]
    identifiers: dict[str, str]
    hosts: set[str]

    def match(self, other: Candidate) -> list[str] | None:
        """같은 법인이면 근거 목록, 다르거나 판단 근거가 없으면 None."""
        if _country(other) != _country(self.candidate):
            return None
        other_ids = _identifiers(other)
        if any(
            scheme in self.identifiers and self.identifiers[scheme] != value
            for scheme, value in other_ids.items()
        ):
            return None
        reasons = []
        if other.candidate_id in self.member_ids:
            reasons.append("candidate_id")
        reasons += [
            f"legal_identifier:{scheme}"
            for scheme, value in sorted(other_ids.items())
            if self.identifiers.get(scheme) == value
        ]
        if _host(other.homepage_url) in self.hosts:
            reasons.append("homepage_host")
        return reasons or None

    def absorb(self, other: Candidate) -> None:
        kept = self.candidate
        aliases = list(kept.aliases)
        for name in [other.canonical_name, *other.aliases]:
            if name != kept.canonical_name and name not in aliases:
                aliases.append(name)
        legal_identifiers = dict(kept.legal_identifiers)
        for scheme, value in other.legal_identifiers.items():
            if scheme.strip().lower() not in self.identifiers:
                legal_identifiers[scheme] = value
        source_ids = list(kept.discovery_source_ids)
        source_ids += [
            s for s in other.discovery_source_ids if s not in kept.discovery_source_ids
        ]
        self.candidate = kept.model_copy(
            update={
                "aliases": aliases,
                "homepage_url": kept.homepage_url or other.homepage_url,
                "legal_identifiers": legal_identifiers,
                "discovery_source_ids": source_ids,
            }
        )
        self.member_ids.add(other.candidate_id)
        self.identifiers |= {
            k: v for k, v in _identifiers(other).items() if k not in self.identifiers
        }
        if (host := _host(other.homepage_url)) is not None:
            self.hosts.add(host)


def normalize_candidates(
    candidates: list[Candidate],
    *,
    max_candidates: int,
    limit_policy: CandidateLimitPolicy | None = None,
) -> NormalizeResult:
    """발견 순서를 유지하며 동일 법인을 첫 후보로 합친다.

    ``max_candidates``는 호출자가 실행 설정(architecture §5)에서 넘긴다. 상한
    이내면 모두 남기고, 초과하면 ``limit_policy``의 선택만 따른다. 코드가 임의
    순서로 자르지 않는다. 같은 candidate_id인데 다른 법인으로 판단되면
    ``ValueError``를 낸다.
    """
    if type(max_candidates) is not int or max_candidates < 1:
        raise ValueError("max_candidates must be a positive integer")

    clusters: list[_Cluster] = []
    merges: list[CandidateMerge] = []
    for candidate in candidates:
        for cluster in clusters:
            reasons = cluster.match(candidate)
            if reasons is not None:
                merges.append(
                    CandidateMerge(
                        kept_candidate_id=cluster.candidate.candidate_id,
                        merged_candidate_id=candidate.candidate_id,
                        matched_on=reasons,
                    )
                )
                cluster.absorb(candidate)
                break
        else:
            if any(candidate.candidate_id in c.member_ids for c in clusters):
                raise ValueError(
                    f"candidate_id {candidate.candidate_id} reused for another entity"
                )
            host = _host(candidate.homepage_url)
            clusters.append(
                _Cluster(
                    candidate=candidate,
                    member_ids={candidate.candidate_id},
                    identifiers=_identifiers(candidate),
                    hosts={host} if host else set(),
                )
            )

    kept = [c.candidate for c in clusters]
    if len(kept) <= max_candidates:
        return NormalizeResult(kept, merges, [])
    if limit_policy is None:
        raise CandidateLimitUnresolved(
            f"{len(kept)} candidates exceed max_candidates={max_candidates} "
            "and no approved limit policy is injected (D08 OPEN)"
        )
    selected = _check_selection(
        limit_policy(list(kept), max_candidates), kept, max_candidates
    )
    by_id = {c.candidate_id: c for c in kept}
    return NormalizeResult(
        candidates=[by_id[i] for i in selected],
        merges=merges,
        dropped_candidate_ids=[
            c.candidate_id for c in kept if c.candidate_id not in selected
        ],
    )


def _check_selection(
    selected: Sequence[str], kept: list[Candidate], max_candidates: int
) -> list[str]:
    ids = list(selected)
    if not all(isinstance(i, str) for i in ids):
        raise ValueError("limit policy must return candidate_id strings")
    if len(set(ids)) != len(ids):
        raise ValueError("limit policy returned duplicate candidate_id")
    unknown = set(ids) - {c.candidate_id for c in kept}
    if unknown:
        raise ValueError(
            f"limit policy returned unknown candidate_id {sorted(unknown)}"
        )
    if not 1 <= len(ids) <= max_candidates:
        raise ValueError("limit policy must keep 1..max_candidates candidates")
    return ids


def discovery_state_update(
    state: InvestmentState,
    outcome: DiscoveryOutcome,
    normalized: NormalizeResult | None,
) -> dict[str, Any]:
    """Discovery 노드의 State patch.

    ``sources``와 ``errors``는 reducer 병합용 신규 값만, reducer가 없는
    ``retrieval_history``는 기존 이력 뒤에 이어 붙인 전체 목록을 담는다.
    제외·병합된 후보의 발견 Source도 이력으로 모두 보존한다.
    """
    if (outcome.status == "found") != (normalized is not None):
        raise ValueError("normalized candidates are required only when found")
    update: dict[str, Any] = {
        "retrieval_history": [
            *state.get("retrieval_history", []),
            *(r.model_dump(mode="json") for r in outcome.retrieval_records),
        ],
        "errors": [e.model_dump(mode="json") for e in outcome.errors],
    }
    if outcome.bundle is not None:
        update["sources"] = {
            source_id: source.model_dump(mode="json")
            for source_id, source in outcome.bundle.sources.items()
        }
    if normalized is not None:
        update["candidates"] = [
            c.model_dump(mode="json") for c in normalized.candidates
        ]
        update["candidate_status"] = {
            c.candidate_id: CandidateStatus.DISCOVERED for c in normalized.candidates
        }
    return update
