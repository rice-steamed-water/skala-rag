"""Synthetic sealed archives only; never actual company facts or approval."""

import hashlib
import importlib
import importlib.util
import json
import socket
import urllib.request
from datetime import UTC, date, datetime

import pytest

from skala_rag.contracts import Candidate, RunInput, ToolBudget
from skala_rag.fakes import FakeClock


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("synthetic archive producer attempted network")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(urllib.request, "urlopen", deny)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def seal(root):
    index = json.loads((root / "collection-index.json").read_text())
    index["file_sha256"] = {
        str(p.relative_to(root)): sha(p.read_bytes())
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != "collection-index.json"
    }
    (root / "collection-index.json").write_text(json.dumps(index))
    return sha((root / "collection-index.json").read_bytes())


def synthetic_archive(root):
    root.mkdir()
    raw = b"<html><title>Synthetic</title><p>Synthetic robot claim</p></html>"
    text = "Synthetic\nSynthetic robot claim\n"
    receipt = dict(
        source_id="synthetic-source",
        candidate="synthetic-company",
        source_class="synthetic_not_actual",
        requested_url="https://synthetic.example/",
        resolved_url="https://synthetic.example/",
        method="public_https_get",
        started_at="2026-10-07T01:00:00Z",
        finished_at="2026-10-07T01:01:00+00:00",
        published_at=None,
        event_date=None,
        status="captured",
        http_status=200,
        raw_path="raw/synthetic-source.html",
        raw_sha256=sha(raw),
        raw_bytes=len(raw),
        extracted_path="text/synthetic-source.txt",
        extracted_sha256=sha(text.encode()),
        extracted_characters=len(text),
        charset="utf-8",
        title="Synthetic",
        publisher="Synthetic publisher",
        language="en",
        metadata=[],
    )
    claim = dict(
        claim_id="synthetic-claim",
        candidate="synthetic-company",
        source_id=receipt["source_id"],
        source_url=receipt["resolved_url"],
        source_class=receipt["source_class"],
        raw_sha256=receipt["raw_sha256"],
        extracted_sha256=receipt["extracted_sha256"],
        retrieved_at=receipt["finished_at"],
        publication_date_observed=None,
        criterion_rating=None,
        review_authority_status="unreviewed_for_production_semantics",
        anchor=dict(
            unit="unicode_character_offset_zero_based",
            start=10,
            end=31,
            sha256_utf8=sha(text[10:31].encode()),
        ),
        summary_ko="합성 미검토 관측",
        authority="synthetic_not_actual",
    )
    manifest = dict(
        artifact_kind="initial_public_company_research_collection_NOT_RunManifest",
        candidate_scope=["synthetic-company"],
        research_as_of="2026-10-07",
        scope_status="synthetic_not_actual",
        records=[receipt],
        capture_get_attempts=1,
        reused_assets=[],
    )
    packet = dict(
        artifact_kind="source_bound_research_packet_NOT_CompanyResearchBundle_or_Evaluation",
        candidate_label="synthetic-company",
        research_as_of="2026-10-07",
        claims=[claim],
        eligibility_status="unknown",
        scores_computed=False,
        final_allowed=False,
        na_rules_applied=False,
        approved_corpus_ingestion_performed=False,
        approved_embedding_or_llm_changed=False,
    )
    crosscheck = dict(queries=[], results=[], search_query_count_in_this_batch=0)
    for name, payload in {
        "README.md": b"Synthetic sealed archive; NOT actual collection",
        receipt["raw_path"]: raw,
        receipt["extracted_path"]: text.encode(),
        "receipts/synthetic-source.json": receipt,
        "manifest.json": manifest,
        "claims/synthetic-company.json": packet,
        "source-crosscheck.json": crosscheck,
    }.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(
            payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        )
    (root / "collection-index.json").write_text(
        json.dumps(
            dict(
                artifact_kind="local_company_collection_integrity_index_NOT_approved_RunManifest",
                sealed_at="2026-10-07T02:00:00Z",
                source_manifest_paths=["manifest.json"],
                candidate_packet_paths=["claims/synthetic-company.json"],
                file_sha256={},
                counts=dict(
                    source_get_attempts_in_batches=1,
                    preliminary_source_get_attempts=0,
                    source_get_attempts_including_preliminary=1,
                    captured_html=1,
                    failed_source_gets_in_batches=0,
                    failed_source_gets_including_preliminary=0,
                    reused_existing_pdfs=0,
                    candidate_packets=1,
                    source_bound_claims=1,
                    assistant_search_queries=0,
                ),
                physical_http_request_total=None,
            )
        )
    )
    return seal(root)


def options(root, pin, *, as_of=date(2026, 10, 7)):
    return dict(
        archive_root=root,
        expected_index_sha256=pin,
        candidate=Candidate(
            schema_version="test",
            candidate_id="synthetic-company",
            canonical_name="Synthetic Company",
            aliases=[],
            country="US",
            homepage_url="https://synthetic.example/",
            legal_identifiers={},
            discovery_source_ids=[],
        ),
        run_input=RunInput(
            schema_version="test",
            investment_theme="Synthetic robotics",
            countries=["US"],
            languages=["en"],
            as_of=as_of,
            policy_version="v3-operational-1.0.0",
            corpus_version="no-corpus",
            execution_mode="live",
        ),
        run_id="synthetic-new-composition",
        budget=ToolBudget(
            schema_version="test", max_calls=1, max_retries=0, timeout_seconds=30
        ),
        clock=FakeClock(datetime(2026, 10, 8, tzinfo=UTC)),
    )


def api():
    assert importlib.util.find_spec("skala_rag.tools.company_archive") is not None, (
        "missing archive-bound Source-only producer"
    )
    return importlib.import_module("skala_rag.tools.company_archive")


def test_synthetic_archive_calls_existing_composer_without_observations(
    tmp_path, monkeypatch
):
    module = api()
    root = tmp_path / "synthetic"
    pin = synthetic_archive(root)
    opts = options(root, pin)
    original = module.LiveResearchCompany.__call__
    seen = []

    def observe(self, candidate, budget):
        seen.append((candidate.candidate_id, self._providers))
        return original(self, candidate, budget)

    monkeypatch.setattr(module.LiveResearchCompany, "__call__", observe)
    result = module.compose_archive_company_research(**opts)
    assert len(seen) == 1
    assert seen[0][1][0].name == "archive-local-sources"
    assert result.status == "ok"
    assert result.data.evidence == result.data.profile.field_evidence_ids == {}
    assert result.data.profile.domain_match is None
    assert result.data.profile.is_listed is None
    assert result.data.profile.exit_completed is None
    assert result.data.profile.stage.normalized_round == "unknown"
    source = result.data.sources["synthetic-source"]
    receipt = json.loads((root / "receipts/synthetic-source.json").read_text())
    assert source.retrieved_at == datetime.fromisoformat(receipt["finished_at"])
    assert source.published_at is None
    assert source.title == receipt["title"]
    assert source.publisher == receipt["publisher"]
    assert source.content_hash == "sha256:" + receipt["raw_sha256"]
    assert source.bibliographic_metadata["original_receipt"] == receipt
    summary = result.retrieval_records[0]
    assert len(result.retrieval_records) == 1
    assert summary.started_at == opts["clock"].now()
    assert summary.arguments_without_secrets["requests_used"] == 0
    assert summary.cost is None
    metadata = summary.arguments_without_secrets["archive_conversion"]
    assert metadata["origin"] == "new_archive_conversion"
    assert metadata["physical_http_requests"] == "unmeasured"
    assert metadata["historical_paid_ledger"] == "not_supplied_unverified"
    assert metadata["post_collection_selection_context"] is True
    assert metadata["candidate_packet"] == json.loads(
        (root / "claims/synthetic-company.json").read_text()
    )
    assert metadata["index_sha256"] == pin
    assert sha((root / "collection-index.json").read_bytes()) == pin


def test_post_composition_raw_replacement_has_no_source_pointer(tmp_path, monkeypatch):
    module = api()
    root = tmp_path / "synthetic"
    pin = synthetic_archive(root)
    opts = options(root, pin)
    result = module.compose_archive_company_research(**opts)
    source = result.data.sources["synthetic-source"]
    assert source.local_path is None
    before = result.model_dump(mode="json")
    receipt = source.bibliographic_metadata["original_receipt"]
    assert source.url == receipt["resolved_url"]
    assert source.content_hash == "sha256:" + receipt["raw_sha256"]

    raw_path = root / receipt["raw_path"]
    raw_path.write_bytes(b"<p>Replaced synthetic raw after composition</p>")
    assert sha(raw_path.read_bytes()) != receipt["raw_sha256"]
    assert source.local_path is None
    assert result.model_dump(mode="json") == before

    def forbidden_composer(*args, **kwargs):
        pytest.fail("changed archive reached existing composer")

    monkeypatch.setattr(module.LiveResearchCompany, "__call__", forbidden_composer)
    with pytest.raises(ValueError, match="archive file hash mismatch"):
        module.compose_archive_company_research(**opts)


def edit_json(root, name, change):
    p = root / name
    value = json.loads(p.read_text())
    change(value)
    p.write_text(json.dumps(value))


@pytest.mark.parametrize(
    "case",
    [
        "file_hash",
        "raw_text_mismatch",
        "raw_hash",
        "anchor",
        "bool_anchor",
        "source_candidate",
        "claim_candidate",
        "claim_source",
        "claim_url",
        "claim_time",
        "claim_hash",
        "duplicate_claim",
        "duplicate_source",
        "receipt_mismatch",
        "missing_receipt",
        "unsealed_text",
        "path_escape",
        "internal_traversal",
        "symlink",
        "directory_symlink",
        "naive_timestamp",
        "reverse_timestamp",
        "missing_timestamp",
        "wrong_count",
        "packet_kind",
        "packet_candidate",
        "positive_eligibility",
        "schema_metadata",
        "run_metadata",
        "duplicate_json_key",
        "nonfinite_json",
        "unsupported_status",
        "oversize",
        "orphan_raw",
        "unlisted_manifest",
        "future_capture",
        "future_copy",
        "unselected_source_dto",
        "reused_source_collision",
        "root_symlink",
        "candidate_metadata",
        "policy_metadata",
        "corpus_metadata",
        "as_of_metadata",
        "missing_title",
        "missing_published_at",
        "malformed_reused_path",
        "malformed_preliminary_failure",
        "later_reused_id_collision",
    ],
)
def test_rejects_invalid_sealed_closure_before_composer(tmp_path, monkeypatch, case):
    module = api()
    root = tmp_path / "synthetic"
    pin = synthetic_archive(root)
    receipt_path = "receipts/synthetic-source.json"
    packet_path = "claims/synthetic-company.json"

    def row(change):
        edit_json(root, "manifest.json", lambda m: change(m["records"][0]))
        edit_json(root, receipt_path, change)

    def claim(change):
        edit_json(root, packet_path, lambda p: change(p["claims"][0]))

    if case == "file_hash":
        (root / "README.md").write_text("tampered")
    elif case == "raw_text_mismatch":
        raw = b"<p>Different synthetic bytes</p>"
        (root / "raw/synthetic-source.html").write_bytes(raw)
        row(lambda r: r.update(raw_sha256=sha(raw), raw_bytes=len(raw)))
        claim(lambda c: c.update(raw_sha256=sha(raw)))
    elif case == "raw_hash":
        row(lambda r: r.update(raw_sha256="0" * 64))
    elif case == "anchor":
        claim(lambda c: c["anchor"].update(end=999))
    elif case == "bool_anchor":
        claim(lambda c: c["anchor"].update(start=True))
    elif case == "source_candidate":
        row(lambda r: r.update(candidate="other"))
    elif case == "claim_candidate":
        claim(lambda c: c.update(candidate="other"))
    elif case == "claim_source":
        claim(lambda c: c.update(source_id="absent"))
    elif case == "claim_url":
        claim(lambda c: c.update(source_url="https://other.example/"))
    elif case == "claim_time":
        claim(lambda c: c.update(retrieved_at="2026-10-06T01:01:00Z"))
    elif case == "claim_hash":
        claim(lambda c: c.update(raw_sha256="0" * 64))
    elif case == "duplicate_claim":
        edit_json(root, packet_path, lambda p: p["claims"].append(p["claims"][0]))
    elif case == "duplicate_source":
        edit_json(root, "manifest.json", lambda m: m["records"].append(m["records"][0]))
    elif case == "receipt_mismatch":
        edit_json(root, receipt_path, lambda r: r.update(title="different"))
    elif case == "missing_receipt":
        (root / receipt_path).unlink()
    elif case == "unsealed_text":
        row(lambda r: r.update(extracted_path="README.md"))
    elif case == "path_escape":
        row(lambda r: r.update(raw_path="../outside.html"))
    elif case == "internal_traversal":
        row(lambda r: r.update(raw_path="raw/../raw/synthetic-source.html"))
    elif case == "symlink":
        p = root / "raw/synthetic-source.html"
        outside = tmp_path / "same.html"
        outside.write_bytes(p.read_bytes())
        p.unlink()
        p.symlink_to(outside)
    elif case == "directory_symlink":
        import shutil

        shutil.move(root / "raw", tmp_path / "outside-raw")
        (root / "raw").symlink_to(tmp_path / "outside-raw", target_is_directory=True)
    elif case == "naive_timestamp":
        row(lambda r: r.update(finished_at="2026-10-07T01:01:00"))
    elif case == "reverse_timestamp":
        row(lambda r: r.update(started_at="2026-10-08T01:01:00Z"))
    elif case == "missing_timestamp":
        row(lambda r: r.update(finished_at=None))
    elif case == "wrong_count":
        edit_json(
            root, "collection-index.json", lambda i: i["counts"].update(captured_html=2)
        )
    elif case == "packet_kind":
        edit_json(root, packet_path, lambda p: p.update(artifact_kind="approved"))
    elif case == "packet_candidate":
        edit_json(root, packet_path, lambda p: p.update(candidate_label="other"))
    elif case == "positive_eligibility":
        edit_json(root, packet_path, lambda p: p.update(eligibility_status="eligible"))
    elif case == "schema_metadata":
        claim(lambda c: c.update(schema_version="historical-forgery"))
    elif case == "run_metadata":
        claim(lambda c: c.update(run_id="historical-forgery"))
    elif case == "candidate_metadata":
        claim(lambda c: c.update(candidate_id="historical-forgery"))
    elif case == "policy_metadata":
        claim(lambda c: c.update(policy_version="historical-forgery"))
    elif case == "corpus_metadata":
        claim(lambda c: c.update(corpus_version="historical-forgery"))
    elif case == "as_of_metadata":
        claim(lambda c: c.update(as_of="2026-10-06"))
    elif case == "missing_title":
        row(lambda r: r.pop("title"))
    elif case == "missing_published_at":
        row(lambda r: r.pop("published_at"))
    elif case == "malformed_reused_path":
        edit_json(
            root,
            "manifest.json",
            lambda m: m["reused_assets"].append(
                dict(
                    source_id="bad-paper",
                    candidate="synthetic-company",
                    method="local_preexisting_approved_pdf_reuse",
                    raw_path=42,
                    raw_sha256="0" * 64,
                    copied_at="2026-10-07T01:30:00Z",
                )
            ),
        )
    elif case == "malformed_preliminary_failure":
        edit_json(
            root,
            "manifest.json",
            lambda m: m.update(
                preliminary_failure_before_batch={"not_a_receipt": True}
            ),
        )
        edit_json(
            root,
            "collection-index.json",
            lambda i: i["counts"].update(
                preliminary_source_get_attempts=1,
                source_get_attempts_including_preliminary=2,
                failed_source_gets_including_preliminary=1,
            ),
        )
    elif case == "later_reused_id_collision":
        original_row = json.loads((root / receipt_path).read_text())
        (root / "raw/reused.pdf").write_bytes(b"%PDF synthetic")
        edit_json(
            root,
            "manifest.json",
            lambda m: m.update(
                records=[],
                capture_get_attempts=0,
                reused_assets=[
                    dict(
                        source_id="synthetic-source",
                        candidate="synthetic-company",
                        method="local_preexisting_approved_pdf_reuse",
                        raw_path="raw/reused.pdf",
                        raw_sha256=sha(b"%PDF synthetic"),
                        copied_at="2026-10-07T01:30:00Z",
                    )
                ],
            ),
        )
        (root / "followup-manifest.json").write_text(
            json.dumps(
                dict(
                    artifact_kind="supplemental_collection_NOT_RunManifest",
                    records=[original_row],
                    additional_source_get_attempts=1,
                    base_manifest_sha256=sha((root / "manifest.json").read_bytes()),
                )
            )
        )
        edit_json(
            root,
            "collection-index.json",
            lambda i: (
                i["source_manifest_paths"].append("followup-manifest.json"),
                i["counts"].update(reused_existing_pdfs=1),
            ),
        )
    elif case == "duplicate_json_key":
        p = root / "source-crosscheck.json"
        p.write_text(
            '{"queries":[],"queries":["shadow"],"results":[],"search_query_count_in_this_batch":0}'
        )
    elif case == "nonfinite_json":
        p = root / "source-crosscheck.json"
        p.write_text(
            '{"queries":[],"results":[],"search_query_count_in_this_batch":0,"bad":NaN}'
        )
    elif case == "unsupported_status":
        row(lambda r: r.update(status="approved"))
    elif case == "oversize":
        (root / "README.md").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    elif case == "orphan_raw":
        (root / "raw/orphan.html").write_bytes(b"unattributed")
    elif case == "unlisted_manifest":
        (root / "followup-manifest.json").write_text(
            json.dumps(
                dict(
                    records=[], artifact_kind="supplemental_collection_NOT_RunManifest"
                )
            )
        )
    elif case == "future_capture":
        row(lambda r: r.update(finished_at="2026-10-08T01:01:00Z"))
        claim(lambda c: c.update(retrieved_at="2026-10-08T01:01:00Z"))
    elif case == "future_copy":
        (root / "raw/reused.pdf").write_bytes(b"%PDF synthetic")
        edit_json(
            root,
            "manifest.json",
            lambda m: m["reused_assets"].append(
                dict(
                    source_id="reused-paper",
                    candidate="synthetic-company",
                    method="local_preexisting_approved_pdf_reuse",
                    raw_path="raw/reused.pdf",
                    raw_sha256=sha(b"%PDF synthetic"),
                    copied_at="2026-10-08T01:00:00Z",
                )
            ),
        )
        edit_json(
            root,
            "collection-index.json",
            lambda i: i["counts"].update(reused_existing_pdfs=1),
        )
    elif case == "unselected_source_dto":
        bad = json.loads((root / receipt_path).read_text())
        bad.update(
            source_id="other-source",
            title=" ",
            raw_path="raw/other-source.html",
            extracted_path="text/other-source.txt",
        )
        (root / "raw/other-source.html").write_bytes(
            (root / "raw/synthetic-source.html").read_bytes()
        )
        (root / "text/other-source.txt").write_bytes(
            (root / "text/synthetic-source.txt").read_bytes()
        )
        (root / "receipts/other-source.json").write_text(json.dumps(bad))
        edit_json(
            root,
            "manifest.json",
            lambda m: (m["records"].append(bad), m.update(capture_get_attempts=2)),
        )
        edit_json(
            root,
            "collection-index.json",
            lambda i: i["counts"].update(
                source_get_attempts_in_batches=2,
                source_get_attempts_including_preliminary=2,
                captured_html=2,
            ),
        )
        # All Source DTOs must be checked before invoking the composer, not just
        # the selected candidate. The added row still needs its own packet.
        bad["candidate"] = "other-company"
        edit_json(
            root,
            "manifest.json",
            lambda m: (
                m["candidate_scope"].append("other-company"),
                m["records"][-1].update(candidate="other-company"),
            ),
        )
        (root / "receipts/other-source.json").write_text(json.dumps(bad))
        packet = json.loads((root / packet_path).read_text())
        packet.update(candidate_label="other-company", claims=[])
        (root / "claims/other-company.json").write_text(json.dumps(packet))
        edit_json(
            root,
            "collection-index.json",
            lambda i: (
                i["candidate_packet_paths"].append("claims/other-company.json"),
                i["counts"].update(candidate_packets=2),
            ),
        )
    elif case == "reused_source_collision":
        (root / "raw/reused.pdf").write_bytes(b"%PDF synthetic")
        edit_json(
            root,
            "manifest.json",
            lambda m: m["reused_assets"].append(
                dict(
                    source_id="synthetic-source",
                    candidate="synthetic-company",
                    method="local_preexisting_approved_pdf_reuse",
                    raw_path="raw/reused.pdf",
                    raw_sha256=sha(b"%PDF synthetic"),
                    copied_at="2026-10-07T01:30:00Z",
                )
            ),
        )
    elif case == "root_symlink":
        link = tmp_path / "root-link"
        link.symlink_to(root, target_is_directory=True)
        root = link
    if case not in {"file_hash", "directory_symlink", "root_symlink"}:
        pin = seal(root)
    from unittest.mock import Mock

    spy = Mock(side_effect=AssertionError("composer reached invalid input"))
    monkeypatch.setattr(module.LiveResearchCompany, "__call__", spy)
    with pytest.raises(ValueError):
        module.compose_archive_company_research(**options(root, pin))
    spy.assert_not_called()


@pytest.mark.parametrize(
    "case",
    [
        "pin_format",
        "wrong_pin",
        "candidate",
        "schema",
        "run",
        "fixture",
        "budget_schema",
        "budget_zero",
        "retries",
        "composition_before_seal",
    ],
)
def test_rejects_invalid_current_context_before_composer(tmp_path, monkeypatch, case):
    module = api()
    root = tmp_path / "synthetic"
    opts = options(root, synthetic_archive(root))
    if case == "pin_format":
        opts["expected_index_sha256"] = " " + opts["expected_index_sha256"]
    elif case == "wrong_pin":
        opts["expected_index_sha256"] = "0" * 64
    elif case == "candidate":
        opts["candidate"].candidate_id = "other"
    elif case == "schema":
        opts["candidate"].schema_version = "other"
    elif case == "run":
        opts["run_id"] = " "
    elif case == "fixture":
        opts["run_input"].execution_mode = "fixture"
    elif case == "budget_schema":
        opts["budget"].schema_version = "other"
    elif case == "budget_zero":
        opts["budget"].max_calls = 0
    elif case == "retries":
        opts["budget"].max_retries = 1
    elif case == "composition_before_seal":
        opts["clock"] = FakeClock(datetime(2026, 10, 7, tzinfo=UTC))
    from unittest.mock import Mock

    spy = Mock(side_effect=AssertionError("composer reached invalid context"))
    monkeypatch.setattr(module.LiveResearchCompany, "__call__", spy)
    with pytest.raises(ValueError):
        module.compose_archive_company_research(**opts)
    spy.assert_not_called()


def test_cutoff_and_repeated_compositions_are_detached(tmp_path):
    module = api()
    root = tmp_path / "synthetic"
    pin = synthetic_archive(root)
    opts = options(root, pin)
    first = module.compose_archive_company_research(**opts)
    first.data.sources["synthetic-source"].bibliographic_metadata["original_claims"][0][
        "summary_ko"
    ] = "mutated"
    assert (
        first.retrieval_records[0].arguments_without_secrets["archive_conversion"][
            "candidate_packet"
        ]["claims"][0]["summary_ko"]
        == "합성 미검토 관측"
    )
    first.retrieval_records[0].arguments_without_secrets["archive_conversion"][
        "candidate_packet"
    ]["claims"][0]["summary_ko"] = "mutated"
    opts["run_id"] = "another-new-composition"
    second = module.compose_archive_company_research(**opts)
    assert (
        second.data.sources["synthetic-source"].bibliographic_metadata[
            "original_claims"
        ][0]["summary_ko"]
        == "합성 미검토 관측"
    )
    assert (
        second.retrieval_records[0].retrieval_id
        != first.retrieval_records[0].retrieval_id
    )
    assert sha((root / "collection-index.json").read_bytes()) == pin
    earlier = module.compose_archive_company_research(
        **options(root, pin, as_of=date(2026, 10, 6))
    )
    assert earlier.status == "empty"
    assert earlier.data.sources == earlier.data.evidence == {}
    assert earlier.retrieval_records[0].arguments_without_secrets[
        "excluded_sources"
    ] == {"synthetic-source": "UNDATED_RETRIEVED_AFTER_AS_OF"}


@pytest.mark.parametrize("published", [None, "2026-10-06", "2026-10-06T14:00:00-04:00"])
def test_source_offset_and_publication_precision_are_not_rewritten(tmp_path, published):
    root = tmp_path / "synthetic"
    synthetic_archive(root)
    finish = "2026-10-07T10:01:00+09:00"
    for path in ("manifest.json", "receipts/synthetic-source.json"):
        edit_json(
            root,
            path,
            lambda p: (p["records"][0] if "records" in p else p).update(
                published_at=published,
                finished_at=finish,
            ),
        )
    edit_json(
        root,
        "claims/synthetic-company.json",
        lambda p: p["claims"][0].update(retrieved_at=finish),
    )
    result = api().compose_archive_company_research(**options(root, seal(root)))
    source = result.data.sources["synthetic-source"]
    assert source.retrieved_at.isoformat() == finish
    assert source.model_dump(mode="json")["published_at"] == published
    assert source.bibliographic_metadata["original_receipt"]["finished_at"] == finish
