"""Code-owned historical content commitments, never execution capabilities.

No network, semantic review, readiness verifier or budget ledger is invoked.
The trusted controller selects this factory; arbitrary receipt JSON is not a
registry. Python code integrity is assumed, not cryptographic authentication.
"""

import hashlib
import json
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Literal

import yaml
from pydantic import JsonValue, TypeAdapter

from skala_rag.agents.moat_verification import (
    CoreArtifactApproval,
    core_artifact_digest,
)
from skala_rag.scoring.approved_policy import ApprovalEvidence, PolicyApprovals
from skala_rag.scoring.v3_policy import V3Policy


@dataclass(frozen=True, slots=True)
class ApprovalPin:
    repository: str
    issue: int
    comment_id: int
    author: str
    scope: str
    version: str
    reference: str
    source_commit: str
    source_path: str
    source_blob: str
    source_sha256: str
    content_sha256: str
    comment_sha256: str
    created_at: str
    updated_at: str
    observed_at: str


# Audited historical records; changing a pin requires code review, not input data.
_PINS = (
    ApprovalPin(
        repository="rice-steamed-water/skala-rag",
        issue=35,
        comment_id=5903505208,
        author="luk0715",
        scope="operational",
        version="v3-operational-1.0.0",
        reference="https://github.com/rice-steamed-water/skala-rag/issues/35#issuecomment-5903505208",
        source_commit="a2f2983717c5d927637b9840294124ef6683b258",
        source_path="configs/scoring.v3.json",
        source_blob="1a27742a626b00412a94db1b206eed9a924bc796",
        source_sha256="1be154fd86148eaff8d7f312aea388406e52ab41dd7f03a8f7daa26d996be59f",
        content_sha256="c99052ef50ad65fc5e38373410aafe7122c8a24994144835944202e44945d594",
        comment_sha256="8eef895ef0bcf96fb72906e14b3f6eefc847441d8bbc0d116803286f616d9ab5",
        created_at="2026-09-30T03:31:40Z",
        updated_at="2026-09-30T03:31:40Z",
        observed_at="2026-10-04T04:48:18.891301+00:00",
    ),
    ApprovalPin(
        repository="rice-steamed-water/skala-rag",
        issue=59,
        comment_id=5904859865,
        author="heojiwon2",
        scope="core",
        version="core-0.1.0",
        reference="https://github.com/rice-steamed-water/skala-rag/issues/59#issuecomment-5904859865",
        source_commit="865f3b146504c61de9dc925b14fc0983a7ad7c8f",
        source_path="configs/rubrics/core.yaml",
        source_blob="97cc05837474823ae9618afc67168e28e3b30d04",
        source_sha256="7d7f64277849659b88d94088be7b94a3ee8efd017bf856719b235b92a35de176",
        content_sha256="24345add802de1f58ff06c6097ce3341e62a39d2f2e56f06912056ae581786a0",
        comment_sha256="6825299c488a3f3062a6989c2016939052d2967ea1e605790a098a78799ae781",
        created_at="2026-09-30T05:39:38Z",
        updated_at="2026-09-30T05:39:38Z",
        observed_at="2026-10-04T04:48:17.887601+00:00",
    ),
    ApprovalPin(
        repository="rice-steamed-water/skala-rag",
        issue=61,
        comment_id=5906253348,
        author="luk0715",
        scope="finance",
        version="finance-0.1.0",
        reference="https://github.com/rice-steamed-water/skala-rag/issues/61#issuecomment-5906253348",
        source_commit="6e45c6a44f9de7c36bab1da53bdf2ab9a2cafe3e",
        source_path="configs/rubrics/finance.yaml",
        source_blob="06039fd0845ecc0f50611b5a86a6a1a3dc0d027b",
        source_sha256="efb56e46202bd384e09413a301e78a3e4ef357ba8eeb68027775ffc04770e149",
        content_sha256="6fea9bd99a2c2a81ecfad72a0f7c15b133ab1176a08b32cb8c69bf3ea8c43d93",
        comment_sha256="54ff76ec1d32946d5a633aed7ca261d0d06c869749962b088f6d66edd1a70b96",
        created_at="2026-09-30T07:22:02Z",
        updated_at="2026-09-30T07:22:02Z",
        observed_at="2026-10-04T04:48:18.385782+00:00",
    ),
)


@dataclass(frozen=True, slots=True)
class ArtifactDiagnosis:
    scope: str
    approval_reference: str
    reference_binding: Literal["matched", "rejected"]
    content_binding: Literal["matched", "rejected"]
    reason: str
    artifact_status: str | None
    owner_dependency: str | None


@dataclass(frozen=True, slots=True)
class AdmissionDiagnosis:
    artifacts: tuple[ArtifactDiagnosis, ...]
    semantic_review: Literal["unreviewed"] = "unreviewed"
    runtime_admission: Literal["not_admitted"] = "not_admitted"
    campaign_approval: Literal["unapproved"] = "unapproved"
    open_decisions: tuple[str, ...] = ("D05", "D06", "D08")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("duplicate or non-string artifact key")
        result[key] = value
    return result


class _UniqueYAML(yaml.SafeLoader):
    """Reject duplicate mapping keys rather than silently dropping content."""


def _yaml_mapping(loader: _UniqueYAML, node: yaml.MappingNode) -> dict:
    # YAML rubric rating maps have integer keys. Keep those original keys.
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in result:
            raise ValueError("duplicate YAML artifact key")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueYAML.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping
)


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class PinnedApprovalRegistry:
    """Local controller configuration, not authenticated artifact proof.

    No row override, verifier callback or ready/budget boolean is accepted.
    Each call reopens content; no positive cache is retained.
    """

    root: Path

    @property
    def pins(self) -> tuple[ApprovalPin, ...]:
        return tuple(replace(pin) for pin in _PINS)

    def policy_approvals(self) -> PolicyApprovals:
        return PolicyApprovals(
            contract_reference="rice-steamed-water/skala-rag#168",
            operational=ApprovalEvidence(
                scope="operational",
                version=_PINS[0].version,
                reference="rice-steamed-water/skala-rag#82",
            ),
            core=ApprovalEvidence(
                scope="core",
                version=_PINS[1].version,
                reference=_PINS[1].reference,
            ),
            finance=ApprovalEvidence(
                scope="finance",
                version=_PINS[2].version,
                reference=_PINS[2].reference,
            ),
        )

    def core_approval(self) -> CoreArtifactApproval:
        pin = _PINS[1]
        return CoreArtifactApproval(pin.reference, pin.version, pin.content_sha256)

    def rubric(self, rubric_version: str) -> dict[str, JsonValue]:
        """Return freshly pinned rubric content, retaining its current header.

        JSON-normalized anchor keys match serialized evaluator input. This is
        content verification, not independent semantic review or live admission.
        """
        pin = next((p for p in _PINS[1:] if p.version == rubric_version), None)
        if pin is None:
            raise ValueError("unknown pinned rubric version")
        payload, digest = self._artifact(pin)
        if digest != pin.content_sha256:
            raise ValueError("current rubric differs from pinned content")
        return TypeAdapter(dict[str, JsonValue]).validate_json(json.dumps(payload))

    def verify_pin(self, claim: object) -> bool:
        """Match the entire provenance row; caller content cannot redefine it."""
        try:
            return type(claim) is ApprovalPin and any(
                all(
                    type(getattr(claim, field.name)) is type(getattr(pin, field.name))
                    and getattr(claim, field.name) == getattr(pin, field.name)
                    for field in fields(ApprovalPin)
                )
                for pin in _PINS
            )
        except Exception:  # Malformed records are not registry authority.
            return False

    def _artifact(self, pin: ApprovalPin) -> tuple[dict, str]:
        raw = (self.root / pin.source_path).read_text(encoding="utf-8")
        payload = (
            json.loads(raw, object_pairs_hook=_unique_pairs)
            if pin.scope == "operational"
            else yaml.load(raw, Loader=_UniqueYAML)
        )
        if type(payload) is not dict:
            raise ValueError("artifact must be an object")
        digest = (
            core_artifact_digest(payload) if pin.scope == "core" else _digest(payload)
        )
        return payload, digest

    def verify_policy(self, evidence: ApprovalEvidence, policy: V3Policy) -> bool:
        """Existing ApprovalVerifier signature; offline content binding only."""
        try:
            if type(evidence) is not ApprovalEvidence or type(policy) is not V3Policy:
                return False
            evidence = ApprovalEvidence.model_validate(evidence.model_dump())
            policy = V3Policy.model_validate(policy.model_dump(warnings="error"))
            expected = getattr(self.policy_approvals(), evidence.scope)
            pin = next(p for p in _PINS if p.scope == evidence.scope)
            if evidence != expected:
                return False
            operational_payload, operational_digest = self._artifact(_PINS[0])
            digest = (
                operational_digest
                if pin.scope == "operational"
                else self._artifact(pin)[1]
            )
            if (
                digest != pin.content_sha256
                or operational_digest != _PINS[0].content_sha256
            ):
                return False
            # Validate the exact parsed content whose digest matched the pin.
            # A later path reload could compare an unapproved file generation.
            snapshot = V3Policy.model_validate(operational_payload)
            return policy == snapshot
        except Exception:  # A failed resolver is rejection, never approval.
            return False

    def verify_core(self, approval: CoreArtifactApproval) -> bool:
        try:
            return (
                type(approval) is CoreArtifactApproval
                and all(
                    type(getattr(approval, field.name)) is str
                    for field in fields(CoreArtifactApproval)
                )
                and approval == self.core_approval()
                and self._artifact(_PINS[1])[1] == _PINS[1].content_sha256
            )
        except Exception:  # A failed resolver is rejection, never approval.
            return False

    def diagnose(
        self, *, approvals: PolicyApprovals | None = None
    ) -> AdmissionDiagnosis:
        """Observe local content/status only; never promote any other gate."""
        artifacts = []
        expected = self.policy_approvals()
        try:
            if approvals is not None and type(approvals) is not PolicyApprovals:
                raise ValueError("explicit PolicyApprovals required")
            supplied = (
                expected
                if approvals is None
                else PolicyApprovals.model_validate(approvals.model_dump())
            )
        except Exception:
            supplied = None
        for pin in _PINS:
            reference_binding = (
                "matched"
                if supplied is not None
                and getattr(supplied, pin.scope) == getattr(expected, pin.scope)
                else "rejected"
            )
            try:
                payload, digest = self._artifact(pin)
                matched = digest == pin.content_sha256
                status = payload.get("status")
                if pin.scope == "operational":
                    status = payload.get("approval", {}).get("status")
                if not isinstance(status, str):
                    status = None
                artifacts.append(
                    ArtifactDiagnosis(
                        pin.scope,
                        pin.reference,
                        reference_binding,
                        "matched" if matched else "rejected",
                        "exact_content" if matched else "content_mismatch",
                        status,
                        "PR134"
                        if pin.scope == "core" and status != "approved"
                        else None,
                    )
                )
            except Exception:  # Reader failure remains a rejected diagnostic.
                artifacts.append(
                    ArtifactDiagnosis(
                        pin.scope,
                        pin.reference,
                        reference_binding,
                        "rejected",
                        "artifact_unreadable",
                        None,
                        None,
                    )
                )
        return AdmissionDiagnosis(tuple(artifacts))


def pinned_approval_registry(root: str | Path) -> PinnedApprovalRegistry:
    """Configure trusted local artifact locations, never inject registry rows."""
    return PinnedApprovalRegistry(Path(root))
