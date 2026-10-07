"""Opt-in retained actual Sources through the EXISTING public SourceOnly consumer.

No live collection, semantic pass, actual E2E scoring, or original HTTP ToolResult.
The fixed candidate names/country/homepages below are post-collection caller inputs.
"""

import hashlib
import json
import os
import socket
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from skala_rag import run_settings, source_only_v3
from skala_rag.agents import m2_research
from skala_rag.agents.eligibility_extraction import LLMEligibilityExtractor
from skala_rag.agents.evidence_collector import EvidenceCollector
from skala_rag.contracts import (
    Candidate,
    DiscoveryBundle,
    RunInput,
    ToolBudget,
    ToolResult,
)
from skala_rag.contracts.tools import CompanyResearchBundle
from skala_rag.fakes import FakeClock
from skala_rag.graph import candidate_workflow_v3 as outer
from skala_rag.rag.hf_embedding import HFEmbeddingEncoder
from skala_rag.tools.company_archive import (
    compose_archive_company_research,
    prepare_reviewed_archive_description,
)
from skala_rag.tools.company_research import LiveResearchCompany
from skala_rag.tools.official_homepage import OfficialHomepage
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM
from skala_rag.tools.source_fetch import SafeFetcher
from skala_rag.tools.structured_llm import OpenAIStructuredLLM


@pytest.fixture
def actual_archive():
    root = os.environ.get("SKALA_COMPANY_ARCHIVE_ROOT")
    pin = os.environ.get("SKALA_COMPANY_ARCHIVE_PIN")
    if root is None and pin is None:
        pytest.skip(
            "actual archive not supplied; synthetic tests are not actual evidence"
        )
    assert root and pin, "actual archive requires BOTH root and external index pin"
    return Path(root), pin


def no_calls(monkeypatch):
    forbidden = Mock(
        side_effect=AssertionError("archive SourceOnly forbidden callback")
    )
    for target, name in (
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
        (httpx.Client, "send"),
        (httpx.AsyncClient, "send"),
        (source_only_v3.SourceOnlyV3, "make_tool"),
        (OfficialHomepage, "__init__"),
        (OfficialHomepage, "__call__"),
        (SafeFetcher, "__init__"),
        (SafeFetcher, "fetch"),
        (LLMEligibilityExtractor, "__call__"),
        (OpenAIStructuredLLM, "__init__"),
        (OpenAIStructuredLLM, "generate"),
        (RuntimeStructuredLLM, "__init__"),
        (RuntimeStructuredLLM, "generate"),
        (HFEmbeddingEncoder, "__init__"),
        (HFEmbeddingEncoder, "embed_texts"),
        (EvidenceCollector, "__call__"),
        (outer, "build_evaluation_graph_v3"),
        (outer, "check_coverage_v3"),
        (outer.snapshot_module, "freeze_snapshot"),
        (outer, "aggregate_scores_v3"),
        (outer, "decide_v3"),
        (outer, "run_candidate_report_v3"),
    ):
        monkeypatch.setattr(target, name, forbidden)
    observed = {
        "assembler": Mock(wraps=source_only_v3.assemble_research_state),
        "eligibility": Mock(wraps=m2_research.check_eligibility),
        "selector": Mock(wraps=outer.select_source_only_terminal_v3),
    }
    monkeypatch.setattr(
        source_only_v3, "assemble_research_state", observed["assembler"]
    )
    monkeypatch.setattr(m2_research, "check_eligibility", observed["eligibility"])
    monkeypatch.setattr(outer, "select_source_only_terminal_v3", observed["selector"])
    original = LiveResearchCompany.__call__
    compositions = []

    def compose(self, candidate, budget):
        compositions.append(candidate.candidate_id)
        outcome = self._providers[0](candidate, Mock())
        assert outcome.observations == outcome.calls == ()
        return original(self, candidate, budget)

    monkeypatch.setattr(LiveResearchCompany, "__call__", compose)
    return forbidden, observed, compositions


@pytest.mark.parametrize(
    "as_of,expected_counts",
    [
        (date(2026, 10, 7), {"physical-intelligence": 7, "skild-ai": 12}),
        (date(2026, 10, 6), {"physical-intelligence": 0, "skild-ai": 0}),
    ],
)
def test_actual_archive_new_compositions_in_public_consumer(
    actual_archive,
    tmp_path,
    monkeypatch,
    as_of,
    expected_counts,
):
    root, pin = actual_archive
    before = (root / "collection-index.json").read_bytes()
    index = json.loads(before)
    assert hashlib.sha256(before).hexdigest() == pin
    assert len(index["file_sha256"]) == 71
    assert index["counts"]["source_bound_claims"] == 22
    forbidden, observed, compositions = no_calls(monkeypatch)
    composition_time = datetime.now(UTC)
    clock = FakeClock(composition_time)
    run_id = f"issue221-new-local-composition-{as_of.isoformat()}-{uuid4().hex}"
    run = RunInput(
        schema_version="issue221-source-only",
        investment_theme="Physical AI / Robotics",
        countries=["US"],
        languages=["en"],
        as_of=as_of,
        policy_version="v3-operational-1.0.0",
        corpus_version="no-corporate-corpus-admission",
        execution_mode="live",
    )
    budget = ToolBudget(
        schema_version=run.schema_version,
        max_calls=1,
        max_retries=0,
        timeout_seconds=30,
    )
    candidates = [
        Candidate(
            schema_version=run.schema_version,
            candidate_id=cid,
            canonical_name=name,
            aliases=[],
            country="US",
            homepage_url=url,
            legal_identifiers={},
            discovery_source_ids=[],
        )
        for cid, name, url in (
            (
                "physical-intelligence",
                "Physical Intelligence",
                "https://www.pi.website",
            ),
            ("skild-ai", "Skild AI", "https://www.skild.ai/"),
        )
    ]
    captures = {
        c.candidate_id: compose_archive_company_research(
            archive_root=root,
            expected_index_sha256=pin,
            candidate=c,
            run_input=run,
            run_id=run_id,
            budget=budget,
            clock=clock,
        )
        for c in candidates
    }
    assert Counter(compositions) == Counter({c.candidate_id: 1 for c in candidates})
    originals = {cid: r.model_dump(mode="json") for cid, r in captures.items()}
    retained = {}
    for cid, captured in captures.items():
        packet = json.loads((root / f"claims/{cid}.json").read_text())
        with pytest.raises(ValidationError):
            ToolResult[CompanyResearchBundle].model_validate(packet)
        assert len(captured.data.sources) == expected_counts[cid]
        assert captured.data.evidence == captured.data.profile.field_evidence_ids == {}
        profile = captured.data.profile
        assert (
            profile.domain_match is profile.is_listed is profile.exit_completed is None
        )
        assert profile.stage.normalized_round == "unknown"
        assert len(captured.retrieval_records) == 1
        record = captured.retrieval_records[0]
        assert record.run_id == run_id and record.candidate_id == cid
        assert record.started_at == record.finished_at == clock.now()
        assert record.cost is None and not captured.errors
        args = record.arguments_without_secrets
        assert args["requests_used"] == 0
        assert args["providers"]["archive-local-sources"]["status"] == "ok"
        meta = args["archive_conversion"]
        assert meta["origin"] == "new_archive_conversion"
        assert meta["post_collection_selection_context"] is True
        assert (
            meta["current_composition"]["scope"] == "local_archive_transformation_only"
        )
        assert meta["current_composition"]["external_requests_used"] == 0
        assert meta["current_composition"]["external_cost_usd"] == 0
        assert meta["collection_index"] == index
        assert meta["candidate_packet"] == packet
        assert meta["search_crosscheck"] == json.loads(
            (root / "source-crosscheck.json").read_text()
        )
        for path in index["source_manifest_paths"]:
            assert meta["source_manifests"][path] == json.loads(
                (root / path).read_text()
            )
        assert meta["physical_http_requests"] == "unmeasured"
        assert (
            meta["historical_paid_ledger"]
            == meta["historical_tool_runtime"]
            == "not_supplied_unverified"
        )
        assert meta["collection_index"]["physical_http_request_total"] is None
        assert (
            meta["collection_index"]["counts"][
                "source_get_attempts_including_preliminary"
            ]
            == 23
        )
        assert (
            meta["collection_index"]["counts"][
                "failed_source_gets_including_preliminary"
            ]
            == 4
        )
        assert len(meta["source_manifests"]["manifest.json"]["reused_assets"]) == 2
        assert all(
            a["source_id"] not in captured.data.sources
            for a in meta["source_manifests"]["manifest.json"]["reused_assets"]
        )
        assert len(meta["search_crosscheck"]["results"]) == 4
        assert all(
            "backend_error" in r["tool_result"]["data"]
            for r in meta["search_crosscheck"]["results"]
        )
        if as_of == date(2026, 10, 6):
            assert (
                len(args["excluded_sources"])
                == {"physical-intelligence": 7, "skild-ai": 12}[cid]
            )
            assert set(args["excluded_sources"].values()) == {
                "UNDATED_RETRIEVED_AFTER_AS_OF"
            }
        for sid, source in captured.data.sources.items():
            receipt = json.loads((root / f"receipts/{sid}.json").read_text())
            assert source.source_id == sid and source.title == receipt["title"]
            assert source.publisher == receipt.get("publisher")
            assert source.url == receipt["resolved_url"]
            assert source.local_path is None
            assert source.content_hash == "sha256:" + receipt["raw_sha256"]
            assert source.retrieved_at == datetime.fromisoformat(receipt["finished_at"])
            assert source.published_at is None
            assert source.bibliographic_metadata["original_receipt"] == receipt
            assert source.bibliographic_metadata["original_claims"] == [
                c for c in packet["claims"] if c["source_id"] == sid
            ]
            retained[sid] = source
    # Fixed candidate provenance explicitly references retained source snapshots,
    # not a fabricated Discovery ToolResult or pre-collection random selection.
    if retained:
        candidates[0].discovery_source_ids = ["pi-sequoia-profile"]
        candidates[1].discovery_source_ids = ["skild-home"]
    bundle = DiscoveryBundle(
        schema_version=run.schema_version, candidates=candidates, sources=retained
    )
    profile = run_settings.recommended_profile(
        run_id=run_id,
        selection_source="post-collection-fixed-candidate-input",
        authority_reference="first-public-collection-and-reuse-not-semantic-approval",
        policy_references=(run.policy_version,),
        code_version=None,
    )
    # Replay later without changing either the acquisition or NEW composition
    # times. This advanced consumer clock is a test, not a future invocation.
    clock.current = composition_time + timedelta(days=1)
    boundary = source_only_v3.prepare_offline_source_only_v3(
        run_id=run_id,
        run_input=run,
        candidate_bundle=bundle,
        research_captures=captures,
        run_profile=profile,
        budget=budget,
        clock=clock,
    )
    # Caller mutation after pinning must not change saved captures.
    captures["skild-ai"].retrieval_records[0].arguments_without_secrets[
        "archive_conversion"
    ]["candidate_packet"]["claims"][0]["summary_ko"] = "caller mutation"
    events = []
    output = tmp_path / f"actual-public-consumer-{as_of.isoformat()}"
    result = source_only_v3.run_source_only_v3(
        boundary, output_dir=output, graph_events=events
    )
    assert boundary.provider is boundary.fetcher_factory is None
    assert result.status == "no_eligible_candidates"
    assert result.selection.reason == "NO_ELIGIBLE_RESULTS"
    assert result.selection.selected_candidate_id is None
    assert (
        result.execution_mode == "live"
        and result.replay_scope == "fixed_candidate_input"
    )
    assert result.candidate_index == 2
    assert result.scores == result.decisions == result.coverage_results == {}
    assert not result.errors
    detail = result.source_only_detail
    assert detail["capture_replay_inputs"] == originals
    for cid in expected_counts:
        assert result.outcomes[cid].status == "eligibility_unknown"
        assert detail["research"][cid]["result"] == originals[cid]
        state = detail["research"][cid]["state"]
        assert state["evidence"] == {}
        assert state["eligibility_results"][cid]["status"] == "unknown"
        assert state["sources"] == originals[cid]["data"]["sources"]
    usage = detail["usage"]
    assert (
        usage["provider_company_research_calls"]
        == usage["provider_configuration_attempts"]
        == 0
    )
    assert usage["captured_replays"] == 2
    assert usage["physical_http_requests"] == "unmeasured"
    nodes = Counter(
        name
        for ns, updates in events
        if not ns
        for name in updates
        if name != "__interrupt__"
    )
    assert nodes == Counter(
        discover=1,
        normalize=1,
        candidate_iterator=3,
        research=2,
        eligibility=2,
        archive=2,
        advance=2,
        selector=1,
    )
    assert {name: call.call_count for name, call in observed.items()} == dict(
        assembler=2, eligibility=2, selector=1
    )
    assert len(compositions) == 2  # Public consumer performed no fresh composition.
    forbidden.assert_not_called()
    saved = json.loads((output / "candidate-run.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    assert saved["source_only_detail"] == detail
    for cid in expected_counts:
        stored = saved["source_only_detail"]["research"][cid]
        assert (
            stored["result"]["retrieval_records"][0]["arguments_without_secrets"][
                "archive_conversion"
            ]["origin"]
            == "new_archive_conversion"
        )
        for sid, source in stored["state"]["sources"].items():
            receipt = json.loads((root / f"receipts/{sid}.json").read_text())
            assert source["local_path"] is None
            assert source["url"] == receipt["resolved_url"]
            assert source["content_hash"] == "sha256:" + receipt["raw_sha256"]
            assert source["bibliographic_metadata"]["original_receipt"] == receipt
    assert manifest["provider"] is None
    assert manifest["semantic_review"] == "unreviewed"
    assert manifest["evaluation"] == manifest["scoring"] == "not_started"
    assert manifest["publication_allowed"] is False
    for name, digest in manifest["artifacts"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    assert (root / "collection-index.json").read_bytes() == before
    for name, digest in index["file_sha256"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    print(
        json.dumps(
            dict(
                actual_public_consumer_output=str(output),
                index_sha256=pin,
                sources=expected_counts,
                source_local_paths_null=True,
                stored_source_local_paths_null=True,
                stored_origin_receipts_urls_raw_hashes_preserved=True,
                source_bound_claims=22,
                verified_files=71,
                new_local_compositions=len(compositions),
                consumer_captured_replays=usage["captured_replays"],
                forbidden_callbacks=forbidden.call_count,
                terminal=result.status,
                selector_reason=result.selection.reason,
                semantic_review="unreviewed",
                evaluation="not_started",
                publication_allowed=False,
            )
        )
    )


def test_actual_reviewed_description_persists_through_original_public_path(
    actual_archive, tmp_path, monkeypatch
):
    names = (
        "SKALA_COMPANY_REVIEW_PROPOSAL",
        "SKALA_COMPANY_REVIEW_PROPOSAL_PIN",
        "SKALA_COMPANY_REVIEW_DECISIONS",
        "SKALA_COMPANY_REVIEW_DECISIONS_PIN",
    )
    inputs = [os.environ.get(name) for name in names]
    if all(value is None for value in inputs):
        pytest.skip(
            "actual user review not supplied; synthetic controls are not user approval"
        )
    assert all(inputs), (
        "actual review requires proposal/decisions paths AND external expected pins"
    )
    root, index_pin = actual_archive
    proposal_path, proposal_pin, decision_path, decision_pin = inputs
    proposal_bytes = Path(proposal_path).read_bytes()
    decision_bytes = Path(decision_path).read_bytes()
    accepted = next(
        d
        for d in json.loads(decision_bytes)["decisions"]
        if d["review_item_id"] == "skild-ai-011"
    )
    before = (root / "collection-index.json").read_bytes()
    index = json.loads(before)
    calls = Counter()

    def forbidden(*args, **kwargs):
        calls["forbidden"] += 1
        raise AssertionError(
            "reviewed local description attempted external/evaluation callback"
        )

    # No mocks for the original archive producer, assembler, Eligibility or outer.
    for target, name in (
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
        (httpx.Client, "send"),
        (httpx.AsyncClient, "send"),
        (source_only_v3.SourceOnlyV3, "make_tool"),
        (OfficialHomepage, "__init__"),
        (OfficialHomepage, "__call__"),
        (SafeFetcher, "__init__"),
        (SafeFetcher, "fetch"),
        (LLMEligibilityExtractor, "__call__"),
        (OpenAIStructuredLLM, "__init__"),
        (OpenAIStructuredLLM, "generate"),
        (RuntimeStructuredLLM, "__init__"),
        (RuntimeStructuredLLM, "generate"),
        (HFEmbeddingEncoder, "__init__"),
        (HFEmbeddingEncoder, "embed_texts"),
        (EvidenceCollector, "__call__"),
        (outer, "build_evaluation_graph_v3"),
        (outer, "check_coverage_v3"),
        (outer.snapshot_module, "freeze_snapshot"),
        (outer, "aggregate_scores_v3"),
        (outer, "decide_v3"),
        (outer, "run_candidate_report_v3"),
    ):
        monkeypatch.setattr(target, name, forbidden)
    run_id = f"issue227-reviewed-local-description-{uuid4().hex}"
    run = RunInput(
        schema_version="issue227-source-only",
        investment_theme="Physical AI / Robotics",
        countries=["US"],
        languages=["en"],
        as_of=date(2026, 10, 7),
        policy_version="v3-operational-1.0.0",
        corpus_version="no-corporate-corpus-admission",
        execution_mode="live",
    )
    candidate = Candidate(
        schema_version=run.schema_version,
        candidate_id="skild-ai",
        canonical_name="Skild AI",
        aliases=[],
        country="US",
        homepage_url="https://www.skild.ai/",
        legal_identifiers={},
        discovery_source_ids=[],
    )
    clock = FakeClock(datetime.now(UTC))
    budget = ToolBudget(
        schema_version=run.schema_version,
        max_calls=1,
        max_retries=0,
        timeout_seconds=30,
    )
    review = prepare_reviewed_archive_description(
        archive_root=root,
        expected_index_sha256=index_pin,
        candidate=candidate,
        run_input=run,
        run_id=run_id,
        proposal_bytes=proposal_bytes,
        expected_proposal_sha256=proposal_pin,
        decision_bytes=decision_bytes,
        expected_decision_sha256=decision_pin,
    )
    capture = compose_archive_company_research(
        archive_root=root,
        expected_index_sha256=index_pin,
        candidate=candidate,
        run_input=run,
        run_id=run_id,
        budget=budget,
        clock=clock,
        reviewed_description=review,
    )
    assert len(capture.data.evidence) == 1 and len(capture.retrieval_records) == 1
    assert len(capture.data.sources) == 12
    eid = next(iter(capture.data.evidence))
    evidence = capture.data.evidence[eid]
    assert evidence.claim == accepted["accepted_statement_ko"]
    assert evidence.limitations == [accepted["limitations_ko"]]
    assert evidence.source_id == "skild-sequoia-profile"
    assert (
        evidence.excerpt
        == accepted["source_evidence"]["supplemental_context_anchors"][0][
            "verbatim_quote"
        ]
    )
    assert evidence.locator.endswith("#unicode-character-offset=84:204")
    assert evidence.scope == "company" and evidence.candidate_id == "skild-ai"
    assert (
        evidence.criterion_ids
        == evidence.supporting_evidence_ids
        == evidence.conflicts_with
        == []
    )
    assert evidence.evidence_kind == "reported" and evidence.confidence == "unknown"
    assert all(
        getattr(evidence, name) is None
        for name in (
            "value",
            "unit",
            "currency",
            "value_as_of",
            "period",
            "geography",
            "event_date",
            "derivation",
            "supersedes",
        )
    )
    record = capture.retrieval_records[0]
    assert evidence.provenance[0].method == "manual"
    assert evidence.provenance[0].retrieval_id == record.retrieval_id
    assert evidence.provenance[0].chunk_id is None and record.chunk_ids == []
    assert record.started_at == record.finished_at == clock.now()
    assert record.arguments_without_secrets["requests_used"] == 0
    assert record.cost is None
    original = capture.model_dump(mode="json")
    clock.current += timedelta(days=1)  # Later consumer clock, not new acquisition.
    profile = run_settings.recommended_profile(
        run_id=run_id,
        selection_source="post-collection-fixed-candidate-input",
        authority_reference="user-reviewed-description-only",
        policy_references=(run.policy_version,),
        code_version=None,
    )
    boundary = source_only_v3.prepare_offline_source_only_v3(
        run_id=run_id,
        run_input=run,
        candidate_bundle=DiscoveryBundle(
            schema_version=run.schema_version,
            candidates=[candidate],
            sources=capture.data.sources,
        ),
        research_captures={"skild-ai": capture},
        reviewed_descriptions={"skild-ai": review},
        run_profile=profile,
        budget=budget,
        clock=clock,
    )
    capture.data.evidence[eid].claim = "caller mutation after pinning"
    output = tmp_path / "actual-reviewed-description"
    final = source_only_v3.run_source_only_v3(boundary, output_dir=output)
    assert final.status == "no_eligible_candidates"
    assert (
        final.selection.reason == "NO_ELIGIBLE_RESULTS"
        and final.selection.selected_candidate_id is None
    )
    assert final.outcomes["skild-ai"].status == "eligibility_unknown"
    assert final.scores == final.decisions == final.coverage_results == {}
    assert not final.errors and calls["forbidden"] == 0
    saved = json.loads((output / "candidate-run.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    detail = saved["source_only_detail"]["research"]["skild-ai"]
    assert detail["result"] == original
    state = detail["state"]
    assert state["evidence"] == original["data"]["evidence"]
    assert state["retrieval_history"][0]["evidence_ids"] == [eid]
    assert (
        state["retrieval_history"][0]["arguments_without_secrets"][
            "reviewed_archive_description"
        ]["decision"]
        == accepted
    )
    assert state["company_profiles"]["skild-ai"]["field_evidence_ids"] == {}
    stored_profile = state["company_profiles"]["skild-ai"]
    assert (
        stored_profile["domain_match"]
        is stored_profile["is_listed"]
        is stored_profile["exit_completed"]
        is None
    )
    assert stored_profile["stage"]["normalized_round"] == "unknown"
    assert state["eligibility_results"]["skild-ai"]["status"] == "unknown"
    assert all(
        c["review_authority_status"] == "unreviewed_for_production_semantics"
        for s in state["sources"].values()
        for c in s["bibliographic_metadata"]["original_claims"]
    )
    assert manifest["semantic_review"] == "unreviewed"
    assert manifest["evaluation"] == manifest["scoring"] == "not_started"
    assert manifest["publication_allowed"] is False
    for name, digest in manifest["artifacts"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    assert (root / "collection-index.json").read_bytes() == before
    for name, digest in index["file_sha256"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    print(
        json.dumps(
            dict(
                actual_reviewed_description_output=str(output),
                evidence_ids=[eid],
                sources=12,
                local_records=1,
                forbidden_callbacks=calls["forbidden"],
                terminal=final.status,
                eligibility="unknown",
                field_evidence_ids={},
                decision_sha256=decision_pin,
                proposal_sha256=proposal_pin,
                index_sha256=index_pin,
                artifact_sha256=manifest["artifacts"],
                semantic_review="unreviewed",
                publication_allowed=False,
            )
        )
    )
