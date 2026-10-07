"""Synthetic hash-pinned review controls, NOT actual user reviews or facts."""

import inspect
import json
import socket
from dataclasses import replace
from datetime import date

import pytest
from tests.unit.test_company_archive import options, seal, sha, synthetic_archive

from skala_rag import run_settings, source_only_v3
from skala_rag.agents.m2_research import assemble_research_state
from skala_rag.agents.m2_trace import TraceInvalid
from skala_rag.contracts import DiscoveryBundle
from skala_rag.tools import company_archive


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    calls = []

    def deny(*args, **kwargs):
        calls.append("network")
        raise AssertionError("reviewed-description control attempted network")

    for target, name in (
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket, "getaddrinfo"),
        (socket, "create_connection"),
    ):
        monkeypatch.setattr(target, name, deny)
    yield calls
    assert calls == []


def controls(tmp_path):
    root = tmp_path / "synthetic"
    synthetic_archive(root)
    # Literal target IDs exercise the narrow port; all content is synthetic.
    for path in list(root.rglob("*")):
        if path.is_file():
            data = path.read_bytes().replace(b"synthetic-company", b"skild-ai")
            data = data.replace(b"synthetic-source", b"skild-sequoia-profile")
            data = data.replace(b"synthetic-claim", b"skild-ai-011")
            path.write_bytes(data)
            new = path.with_name(
                path.name.replace("synthetic-company", "skild-ai").replace(
                    "synthetic-source", "skild-sequoia-profile"
                )
            )
            if new != path:
                path.rename(new)
    pin = seal(root)
    opts = options(root, pin)
    opts["candidate"].candidate_id = "skild-ai"
    claim = json.loads((root / "claims/skild-ai.json").read_text())["claims"][0]
    quote = dict(
        source_id=claim["source_id"],
        extracted_sha256=claim["extracted_sha256"],
        anchor=claim["anchor"],
        verbatim_quote="Synthetic robot claim",
    )
    proposal = dict(
        candidate_label="skild-ai",
        research_as_of="2026-10-07",
        archive_index_sha256=pin,
        items=[
            dict(
                review_item_id="skild-ai-011",
                proposed_statement_ko="Synthetic attributed description",
                limitations_ko="Synthetic limitation; not identity",
                original_claim=claim,
                supplemental_context_anchors=[quote],
            )
        ],
    )
    proposal_bytes = json.dumps(proposal).encode()
    decision = dict(
        review_item_id="skild-ai-011",
        accepted_statement_ko=proposal["items"][0]["proposed_statement_ko"],
        limitations_ko=proposal["items"][0]["limitations_ko"],
        decision="accepted_with_stated_limits",
        reviewer_identity="synthetic test control",
        source_evidence=dict(
            original_claim_id=claim["claim_id"],
            original_source_id=claim["source_id"],
            original_text_sha256=claim["extracted_sha256"],
            original_anchor=claim["anchor"],
            supplemental_context_anchors=[quote],
        ),
    )
    decision_bytes = json.dumps(
        dict(
            proposal_sha256=sha(proposal_bytes),
            archive_index_sha256=pin,
            research_as_of="2026-10-07",
            decisions=[decision],
        )
    ).encode()
    review = dict(
        proposal_bytes=proposal_bytes,
        expected_proposal_sha256=sha(proposal_bytes),
        decision_bytes=decision_bytes,
        expected_decision_sha256=sha(decision_bytes),
    )
    return opts, review


def prepared(opts, review):
    return company_archive.prepare_reviewed_archive_description(
        **{
            k: opts[k]
            for k in (
                "archive_root",
                "expected_index_sha256",
                "candidate",
                "run_input",
                "run_id",
            )
        },
        **review,
    )


def test_original_producer_and_consumer_preserve_field_free_description(tmp_path):
    opts, review = controls(tmp_path)
    kwargs = {}
    if (
        "reviewed_description"
        in inspect.signature(
            company_archive.compose_archive_company_research
        ).parameters
    ):
        kwargs["reviewed_description"] = prepared(opts, review)
    result = company_archive.compose_archive_company_research(**opts, **kwargs)
    assert len(result.data.evidence) == 1, (
        "original archive producer drops reviewed description"
    )
    assembled = assemble_research_state(
        candidate=opts["candidate"],
        run_input=opts["run_input"],
        run_id=opts["run_id"],
        result=result,
        **kwargs,
    )
    assert len(assembled.state["evidence"]) == 1
    evidence = next(iter(result.data.evidence.values()))
    assert evidence.claim == "Synthetic attributed description"
    assert evidence.limitations == ["Synthetic limitation; not identity"]
    assert evidence.provenance[0].method == "manual"
    assert (
        evidence.provenance[0].retrieval_id == result.retrieval_records[0].retrieval_id
    )
    assert evidence.criterion_ids == []
    assert evidence.confidence == "unknown" and evidence.evidence_kind == "reported"
    assert result.data.profile.field_evidence_ids == {}
    assert assembled.receipt["eligibility_status"] == "unknown"
    assert assembled.state["candidate_status"]["skild-ai"] == "eligibility_unknown"
    assert (
        result.data.sources["skild-sequoia-profile"].bibliographic_metadata[
            "original_claims"
        ][0]["review_authority_status"]
        == "unreviewed_for_production_semantics"
    )
    assert (
        sha((opts["archive_root"] / "collection-index.json").read_bytes())
        == opts["expected_index_sha256"]
    )


def test_public_source_only_propagates_protected_review_context(tmp_path):
    opts, review = controls(tmp_path)
    context = prepared(opts, review)
    result = company_archive.compose_archive_company_research(
        **opts, reviewed_description=context
    )
    kwargs = {}
    if (
        "reviewed_descriptions"
        in inspect.signature(source_only_v3.prepare_offline_source_only_v3).parameters
    ):
        kwargs["reviewed_descriptions"] = {"skild-ai": context}
    boundary = source_only_v3.prepare_offline_source_only_v3(
        run_id=opts["run_id"],
        run_input=opts["run_input"],
        candidate_bundle=DiscoveryBundle(
            schema_version="test",
            candidates=[opts["candidate"]],
            sources=result.data.sources,
        ),
        research_captures={"skild-ai": result},
        run_profile=run_settings.recommended_profile(
            run_id=opts["run_id"],
            selection_source="synthetic controls",
            authority_reference="synthetic NOT approval",
            policy_references=(opts["run_input"].policy_version,),
            code_version=None,
        ),
        budget=opts["budget"],
        clock=opts["clock"],
        **kwargs,
    )
    final = source_only_v3.run_source_only_v3(
        boundary, output_dir=tmp_path / "public-output"
    )
    assert final.status == "no_eligible_candidates", (
        "public consumer rejected reviewed description"
    )
    state = final.source_only_detail["research"]["skild-ai"]["state"]
    assert len(state["evidence"]) == 1
    assert state["company_profiles"]["skild-ai"]["field_evidence_ids"] == {}
    assert final.scores == final.decisions == {}


@pytest.mark.parametrize(
    "change",
    [
        "claim",
        "limitations",
        "excerpt",
        "locator",
        "criterion",
        "value",
        "source",
        "source_metadata",
        "anchor",
        "archive_pin",
        "review_pin",
        "marker",
        "missing_record",
        "failed_record",
        "missing_link",
        "field_map",
        "candidate",
        "run",
        "schema",
        "as_of",
        "corpus",
        "canonical",
        "extra_source",
        "arbitrary_manual",
        "stage_context",
        "provider_identity",
        "result_schema",
        "bundle_schema",
    ],
)
def test_original_consumer_rejects_changed_reviewed_conversion(tmp_path, change):
    opts, review = controls(tmp_path)
    context = prepared(opts, review)
    result = company_archive.compose_archive_company_research(
        **opts, reviewed_description=context
    )
    evidence = next(iter(result.data.evidence.values()))
    record = result.retrieval_records[0]
    marker = record.arguments_without_secrets["reviewed_archive_description"]
    conversion = record.arguments_without_secrets["archive_conversion"]
    if change == "claim":
        evidence.claim = "Changed attributed claim"
    elif change == "limitations":
        evidence.limitations = ["Changed limitation"]
    elif change == "excerpt":
        evidence.excerpt = "Changed excerpt"
    elif change == "locator":
        evidence.locator += "-changed"
    elif change == "criterion":
        evidence.criterion_ids = ["technology.integration"]
    elif change == "value":
        evidence.value = 1
    elif change == "source":
        result.data.sources[evidence.source_id].url = "https://wrong.example/"
    elif change == "source_metadata":
        result.data.sources[evidence.source_id].bibliographic_metadata[
            "original_receipt"
        ]["extracted_sha256"] = "0" * 64
    elif change == "anchor":
        marker["decision"]["source_evidence"]["original_anchor"]["start"] = 0
    elif change == "archive_pin":
        conversion["index_sha256"] = "0" * 64
    elif change == "review_pin":
        marker["decision_sha256"] = "0" * 64
    elif change == "marker":
        marker["review_item_id"] = "skild-ai-013"
    elif change == "missing_record":
        result.retrieval_records = []
    elif change == "failed_record":
        record.status = "empty"
    elif change == "missing_link":
        record.evidence_ids = []
    elif change == "field_map":
        result.data.profile.field_evidence_ids = {"business": [evidence.evidence_id]}
    elif change == "stage_context":
        result.data.profile.stage.raw_label = "Series C"
    elif change == "result_schema":
        result.schema_version = "other-schema"
    elif change == "bundle_schema":
        result.data.schema_version = "other-schema"
    elif change == "provider_identity":
        record.arguments_without_secrets["providers"] = {
            "arbitrary-manual": {"required": True, "notes": []}
        }
    elif change == "candidate":
        opts["candidate"].candidate_id = "other"
    elif change == "run":
        opts["run_id"] = "other-run"
    elif change == "schema":
        opts["run_input"].schema_version = "other-schema"
    elif change == "as_of":
        opts["run_input"].as_of = date(2026, 10, 8)
    elif change == "corpus":
        opts["run_input"].corpus_version = "other-corpus"
    elif change == "canonical":
        opts["candidate"].canonical_name = "Other caller candidate"
        record.query = opts["candidate"].canonical_name
    elif change == "extra_source":
        extra = result.data.sources[evidence.source_id].model_copy(deep=True)
        extra.source_id = "unrelated-source"
        result.data.sources[extra.source_id] = extra
        record.source_ids.append(extra.source_id)
    elif change == "arbitrary_manual":
        context = None  # The valid-looking marker is not controller authorization.
    with pytest.raises((TraceInvalid, ValueError)):
        assemble_research_state(
            candidate=opts["candidate"],
            run_input=opts["run_input"],
            run_id=opts["run_id"],
            result=result,
            reviewed_description=context,
        )


@pytest.mark.parametrize(
    "change",
    [
        "proposal_pin",
        "decision_pin",
        "index_pin",
        "unapproved",
        "other_claim",
        "statement",
        "limits",
        "source",
        "anchor",
        "quote",
        "reviewer",
        "archive",
        "date",
    ],
)
def test_preparation_rejects_unmatched_review_inputs(tmp_path, change):
    opts, review = controls(tmp_path)
    decision = json.loads(review["decision_bytes"])
    if change == "proposal_pin":
        review["expected_proposal_sha256"] = "0" * 64
    elif change == "decision_pin":
        review["expected_decision_sha256"] = "0" * 64
    elif change == "index_pin":
        opts["expected_index_sha256"] = "0" * 64
    else:
        item = decision["decisions"][0]
        if change == "unapproved":
            item["decision"] = "unreviewed"
        elif change == "other_claim":
            item["review_item_id"] = "skild-ai-013"
        elif change == "statement":
            item["accepted_statement_ko"] = "Changed"
        elif change == "limits":
            item["limitations_ko"] = "Changed"
        elif change == "source":
            item["source_evidence"]["original_source_id"] = "other-source"
        elif change == "anchor":
            item["source_evidence"]["original_anchor"]["start"] = 0
        elif change == "quote":
            item["source_evidence"]["supplemental_context_anchors"][0][
                "verbatim_quote"
            ] = "Changed"
        elif change == "reviewer":
            item["reviewer_identity"] = ""
        elif change == "archive":
            decision["archive_index_sha256"] = "0" * 64
        elif change == "date":
            decision["research_as_of"] = "2026-10-06"
        review["decision_bytes"] = json.dumps(decision).encode()
        # Test-owned re-pinning is synthetic authority, not a user review.
        review["expected_decision_sha256"] = sha(review["decision_bytes"])
    with pytest.raises((ValueError, KeyError)):
        prepared(opts, review)


def test_repetition_detaches_review_results_and_consumer_never_reopens_paths(tmp_path):
    opts, review = controls(tmp_path)
    context = prepared(opts, review)
    first = company_archive.compose_archive_company_research(
        **opts, reviewed_description=context
    )
    second = company_archive.compose_archive_company_research(
        **opts, reviewed_description=context
    )
    original = second.model_dump(mode="json")
    first.data.evidence[next(iter(first.data.evidence))].limitations.append(
        "caller mutation"
    )
    first.retrieval_records[0].arguments_without_secrets[
        "reviewed_archive_description"
    ]["decision"]["limitations_ko"] = "caller mutation"
    assert second.model_dump(mode="json") == original
    (opts["archive_root"] / "text/skild-sequoia-profile.txt").write_text(
        "replaced AFTER snapshots"
    )
    assembled = assemble_research_state(
        candidate=opts["candidate"],
        run_input=opts["run_input"],
        run_id=opts["run_id"],
        result=second,
        reviewed_description=context,
    )
    assert assembled.receipt["eligibility_status"] == "unknown"
    assert second.model_dump(mode="json") == original
    with pytest.raises(ValueError, match="hash mismatch"):
        company_archive.compose_archive_company_research(
            **opts, reviewed_description=context
        )


def test_public_binding_detaches_and_rejects_replaced_review_context(tmp_path):
    opts, review = controls(tmp_path)
    context = prepared(opts, review)
    capture = company_archive.compose_archive_company_research(
        **opts, reviewed_description=context
    )
    reviews = {"skild-ai": context}
    boundary = source_only_v3.prepare_offline_source_only_v3(
        run_id=opts["run_id"],
        run_input=opts["run_input"],
        candidate_bundle=DiscoveryBundle(
            schema_version="test",
            candidates=[opts["candidate"]],
            sources=capture.data.sources,
        ),
        research_captures={"skild-ai": capture},
        reviewed_descriptions=reviews,
        run_profile=run_settings.recommended_profile(
            run_id=opts["run_id"],
            selection_source="synthetic controls",
            authority_reference="synthetic NOT approval",
            policy_references=(opts["run_input"].policy_version,),
            code_version=None,
        ),
        budget=opts["budget"],
        clock=opts["clock"],
    )
    reviews["skild-ai"] = "caller mutation"
    assert json.loads(boundary.reviewed_descriptions_json) == {"skild-ai": context}
    changed = replace(boundary, reviewed_descriptions_json="{}")
    assert changed.binding_digest() != boundary.input_binding_sha256
    with pytest.raises(ValueError, match="binding mismatch"):
        source_only_v3.run_source_only_v3(changed, output_dir=tmp_path / "refused")
    assert not (tmp_path / "refused").exists()
    replaced = replace(
        boundary, reviewed_descriptions_json=json.dumps({"skild-ai": context + " "})
    )
    with pytest.raises(ValueError, match="binding mismatch"):
        replaced.research(opts["candidate"], None)
