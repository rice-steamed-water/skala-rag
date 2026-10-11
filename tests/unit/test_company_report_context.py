"""Synthetic originals and vectors; real provenance and writer boundaries."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from typing import NotRequired, TypedDict
from urllib.parse import quote

import pytest
from tests.unit.test_company_store import (
    SyntheticEncoder,
    accepted_report,
    identity,
    material,
    query,
    settings,
)

from skala_rag.contracts.company_report import (
    QUESTION_IDS,
    EvidenceView,
    InformationResult,
    NarrativeQuestion,
)
from skala_rag.rag.company_store import (
    CompanyStore,
    StoreManifest,
    StoreSnapshot,
    evidence_fingerprint,
)
from skala_rag.reporting.company_context import (
    CompanyContextError,
    IssuerAssociation,
    build_writer_context,
    load_issuer_association,
    resolve_company_evidence,
)

AS_OF = date(2026, 10, 10)


def competitor_material(name="b"):
    item = material(name)
    return replace(
        item,
        document=item.document.model_copy(update={"candidate_ids": ("co-b",)}),
        chunks=(item.chunks[0].model_copy(update={"candidate_ids": ["co-b"]}),),
        evidence=(item.evidence[0].model_copy(update={"candidate_id": "co-b"}),),
        retrieval_records=(
            item.retrieval_records[0].model_copy(update={"candidate_id": "co-b"}),
        ),
    )


def lineage_store(tmp_path):
    store = CompanyStore(
        tmp_path / "store", settings=settings(), encoder=SyntheticEncoder()
    )
    store.ingest_sources((material(),), companies=(identity(),))
    for number in (1, 2):
        store.ingest_report(
            accepted_report(
                report_id=f"prior-{number}",
                markdown=f"Synthetic interpretation {number}. [@evidence:ev-a]",
            )
        )
    retained = store.open()
    assert retained is not None
    return retained


def test_report_reuse_does_not_multiply_support(tmp_path):
    # Given two reports derived from the same original.
    retained = lineage_store(tmp_path)
    ids = tuple(hit.chunk.chunk_id for hit in retained.search(query(), top_k=50))
    # When the retained production resolver closes report lineage.
    resolved = resolve_company_evidence(
        retained, ids, candidate_id="co-a", as_of=AS_OF, current_report_id="current"
    )
    # Then generated prose contributes no independent evidence.
    assert {item.evidence_id for item in resolved.evidence} == {"ev-a"}
    assert {item["report_id"] for item in resolved.prior_interpretations} == {
        "prior-1",
        "prior-2",
    }
    assert all(
        item["original_evidence_ids"] == ["ev-a"]
        for item in resolved.prior_interpretations
    )
    assert all(
        item["independent_support"] is False for item in resolved.prior_interpretations
    )


@pytest.mark.parametrize("damage", ["missing", "cycle", "fingerprint", "self"])
def test_invalid_report_lineage_never_adds_support(tmp_path, damage):
    stored = lineage_store(tmp_path)
    manifest = stored.manifest
    report = manifest.reports["prior-1"]
    current = "current"
    if damage == "missing":
        del manifest.evidence["ev-a"]
    elif damage == "cycle":
        report.parent_report_ids = ("prior-1",)
    elif damage == "fingerprint":
        report.original_evidence_hashes["ev-a"] = "changed"
    else:
        current = "prior-1"
    damaged = StoreSnapshot(stored.root, manifest.model_dump_json().encode())
    ids = [
        c.chunk_id for c in manifest.chunks.values() if c.source_id in report.source_ids
    ]
    resolved = resolve_company_evidence(
        damaged, ids, candidate_id="co-a", as_of=AS_OF, current_report_id=current
    )
    assert resolved.evidence == ()
    assert resolved.prior_interpretations == ()


class WriterInputs(TypedDict):
    retained: StoreSnapshot
    company_name: str
    as_of: date
    questions: list[NarrativeQuestion]
    evidence_ids: list[str]
    issuer_association: NotRequired[IssuerAssociation]
    may_request_information: NotRequired[bool]
    information_results: NotRequired[list[InformationResult]]


def narrative_questions() -> list[NarrativeQuestion]:
    return [
        NarrativeQuestion.model_validate(
            {
                "schema_version": "narrative-report-1",
                "question_id": qid,
                "domain": qid.rsplit(".", 1)[0],
                "question": f"Controlled question {number}",
            }
        )
        for number, qid in enumerate(QUESTION_IDS)
    ]


@pytest.fixture
def writer_inputs(tmp_path) -> WriterInputs:
    store = CompanyStore(
        tmp_path / "writer-store", settings=settings(), encoder=SyntheticEncoder()
    )
    stored = store.ingest_sources(
        (
            material(),
            material("support", text="Controlled supporting observation."),
            material(
                "industry", industry=True, text="Controlled industry observation."
            ),
            competitor_material(),
        ),
        companies=(identity(),),
    )
    return dict(
        retained=stored,
        company_name="Synthetic a Robotics",
        as_of=AS_OF,
        questions=narrative_questions(),
        evidence_ids=["ev-a", "ev-industry"],
    )


def altered_store(
    inputs: WriterInputs, change: Callable[[StoreManifest], None]
) -> WriterInputs:
    stored = inputs["retained"]
    manifest = stored.manifest
    change(manifest)
    return {
        **inputs,
        "retained": StoreSnapshot(stored.root, manifest.model_dump_json().encode()),
    }


def test_writer_corrupted_original_rejected(writer_inputs: WriterInputs):
    # Given an accepted writer projection backed by an exact retained original.
    build_writer_context(**writer_inputs).verify_binding()
    stored = writer_inputs["retained"]
    document = stored.manifest.documents["src-a"]
    # When the original bytes change without replacing the admitted snapshot.
    (stored.root / document.local_path).write_bytes(b"Altered original quotation.")
    # Then fresh projection fails on physical corruption.
    with pytest.raises(CompanyContextError, match="ORIGINAL_BYTES_MISMATCH"):
        build_writer_context(**writer_inputs)


def test_writer_projection_keeps_only_allowlisted_facts_and_private_originals(
    writer_inputs: WriterInputs,
):
    stored = writer_inputs["retained"]
    original = stored.payload
    context = build_writer_context(**writer_inputs)
    context.verify_binding()
    writer = context.writer_context
    assert set(writer.model_dump()) == {
        "schema_version",
        "context_id",
        "target",
        "as_of",
        "questions",
        "evidence",
        "may_request_information",
        "information_results",
    }
    assert set(writer.evidence["ev-a"].model_dump()) == set(EvidenceView.model_fields)
    assert writer.evidence["ev-industry"].scope == "industry"
    assert (
        writer.evidence["ev-a"].limitations
        == stored.manifest.evidence["ev-a"].limitations
    )
    citation = json.loads(context.citation_payload)["citations"]["ev-a"]
    assert citation["original_evidence"] == stored.manifest.evidence["ev-a"].model_dump(
        mode="json"
    )
    assert citation["locator"] == stored.manifest.chunks["chunk-a"].locator
    assert "ret-a" in citation["retrieval_records"]
    assert stored.payload == original
    # Returned mutable DTOs cannot change the bound serialized generation.
    writer.evidence.clear()
    context.citation_registry.clear()
    context.verify_binding()
    assert set(context.writer_context.evidence) == {"ev-a", "ev-industry"}


def test_writer_subject_uses_reviewed_issuer_without_legal_identity(
    writer_inputs: WriterInputs,
):
    stored = writer_inputs["retained"]
    manifest = stored.manifest
    manifest.companies.clear()
    evidence = manifest.evidence["ev-a"]
    association = IssuerAssociation(
        subject_id="co-a",
        name="Synthetic a Robotics",
        evidence_hashes={
            "ev-a": evidence_fingerprint(evidence, manifest.sources["src-a"])
        },
    )
    inputs: WriterInputs = {
        **writer_inputs,
        "retained": StoreSnapshot(stored.root, manifest.model_dump_json().encode()),
        "issuer_association": association,
    }
    context = build_writer_context(**inputs)
    assert context.writer_context.target.subject_id == "co-a"
    assert set(context.writer_context.target.model_dump()) == {
        "schema_version",
        "subject_id",
        "name",
    }
    inputs["issuer_association"] = replace(
        association, evidence_hashes={"ev-a": "forged"}
    )
    with pytest.raises(CompanyContextError, match="ISSUER_ASSOCIATION_MISMATCH"):
        build_writer_context(**inputs)


@pytest.fixture
def issuer_receipt_data(writer_inputs):
    manifest = writer_inputs["retained"].manifest
    return {
        "schema_version": "issuer-association-1",
        "subject_id": "co-a",
        "name": writer_inputs["company_name"],
        "evidence_hashes": {
            eid: evidence_fingerprint(
                manifest.evidence[eid],
                manifest.sources[manifest.evidence[eid].source_id],
            )
            for eid in ("ev-a", "ev-support")
        },
    }


def test_issuer_receipt_loads_all_reviewed_ids_and_binds_exact_bytes(
    tmp_path, writer_inputs, issuer_receipt_data
):
    # Given
    inputs = altered_store(writer_inputs, lambda m: m.companies.clear())
    original = inputs["retained"].payload
    path = tmp_path / "private-issuer.json"
    content = json.dumps(issuer_receipt_data).encode()
    path.write_bytes(content)
    # When
    association = load_issuer_association(
        path,
        retained=inputs["retained"],
        company_name=inputs["company_name"],
        as_of=AS_OF,
    )
    assert association is not None
    inputs["issuer_association"] = association
    context = build_writer_context(**inputs)
    # Then
    assert association is not None
    assert dict(association.evidence_hashes) == issuer_receipt_data["evidence_hashes"]
    assert association.receipt_hash == "sha256:" + hashlib.sha256(content).hexdigest()
    assert not hasattr(association.evidence_hashes, "__setitem__")
    context.verify_binding()
    assert set(context.writer_context.evidence) == {"ev-a", "ev-support", "ev-industry"}
    private = json.loads(context.citation_payload)["issuer_association"]
    assert private["receipt_hash"] == association.receipt_hash
    assert private["evidence_hashes"] == issuer_receipt_data["evidence_hashes"]
    assert "issuer_association" not in context.writer_context.model_dump()
    assert inputs["retained"].payload == original


def test_issuer_receipt_formatting_changes_private_binding_not_writer_facts(
    tmp_path, writer_inputs, issuer_receipt_data
):
    # Given
    path = tmp_path / "issuer.json"
    path.write_text(json.dumps(issuer_receipt_data), encoding="utf-8")
    first = load_issuer_association(
        path,
        retained=writer_inputs["retained"],
        company_name=writer_inputs["company_name"],
        as_of=AS_OF,
    )
    assert first is not None
    inputs = writer_inputs.copy()
    inputs["issuer_association"] = first
    original = build_writer_context(**inputs)
    path.write_text(json.dumps(issuer_receipt_data, indent=2), encoding="utf-8")
    # When
    second = load_issuer_association(
        path,
        retained=writer_inputs["retained"],
        company_name=writer_inputs["company_name"],
        as_of=AS_OF,
    )
    assert second is not None
    inputs["issuer_association"] = second
    changed = build_writer_context(**inputs)
    # Then
    assert original.writer_context.evidence == changed.writer_context.evidence
    assert original.context_id != changed.context_id
    with pytest.raises(CompanyContextError, match="BINDING_MISMATCH"):
        replace(original, citation_payload=changed.citation_payload).verify_binding()


@pytest.mark.parametrize(
    "damage",
    ["name", "subject", "foreign", "stale", "unknown", "industry", "bytes", "cutoff"],
)
def test_issuer_receipt_rejects_untrusted_originals(
    tmp_path, writer_inputs, issuer_receipt_data, damage
):
    # Given
    inputs = writer_inputs
    match damage:
        case "name":
            issuer_receipt_data["name"] = "Different Robotics"
        case "subject":
            issuer_receipt_data["subject_id"] = "co-b"
        case "foreign" | "industry":
            eid = "ev-b" if damage == "foreign" else "ev-industry"
            manifest = inputs["retained"].manifest
            item = manifest.evidence[eid]
            issuer_receipt_data["evidence_hashes"][eid] = evidence_fingerprint(
                item, manifest.sources[item.source_id]
            )
        case "stale":
            issuer_receipt_data["evidence_hashes"]["ev-support"] = "sha256:" + "0" * 64
        case "unknown":
            issuer_receipt_data["evidence_hashes"]["absent"] = "sha256:" + "0" * 64
        case "bytes":
            stored = inputs["retained"]
            doc = stored.manifest.documents["src-support"]
            (stored.root / doc.local_path).write_bytes(b"Changed original")
        case "cutoff":
            inputs = altered_store(
                inputs,
                lambda m: setattr(
                    m.evidence["ev-support"], "event_date", date(2026, 10, 11)
                ),
            )
    path = tmp_path / "issuer.json"
    path.write_text(json.dumps(issuer_receipt_data), encoding="utf-8")
    # When / Then
    with pytest.raises(CompanyContextError):
        load_issuer_association(
            path,
            retained=inputs["retained"],
            company_name=inputs["company_name"],
            as_of=AS_OF,
        )


@pytest.mark.parametrize(
    "text",
    [
        "{",
        "null",
        "[]",
        '{"schema_version":"issuer-association-1","name":"A","name":"B"}',
        '{"schema_version":"issuer-association-1","subject_id":"a","name":"A",'
        '"evidence_hashes":{"ev-a":"x","ev-a":"y"}}',
        '{"schema_version":"issuer-association-1","subject_id":"a","name":"A",'
        '"evidence_hashes":{"ev-a":NaN}}',
    ],
)
def test_issuer_receipt_rejects_malformed_json(tmp_path, writer_inputs, text):
    # Given
    path = tmp_path / "issuer.json"
    path.write_text(text, encoding="utf-8")
    # When / Then
    with pytest.raises(ValueError):
        load_issuer_association(
            path,
            retained=writer_inputs["retained"],
            company_name=writer_inputs["company_name"],
            as_of=AS_OF,
        )


@pytest.mark.parametrize(
    "override",
    [
        {"schema_version": "issuer-association-2"},
        {"subject_id": True},
        {"name": " "},
        {"evidence_hashes": {}},
        {"evidence_hashes": {"ev-a": "forged"}},
        {"evidence_hashes": {"ev-a": 1}},
        {"evidence_hashes": {" ": "sha256:" + "a" * 64}},
        {"legal_identifiers": {}},
        {"approved": True},
    ],
)
def test_issuer_receipt_rejects_malformed_schema(
    tmp_path, writer_inputs, issuer_receipt_data, override
):
    # Given
    path = tmp_path / "issuer.json"
    path.write_text(json.dumps(issuer_receipt_data | override), encoding="utf-8")
    # When / Then
    with pytest.raises(ValueError):
        load_issuer_association(
            path,
            retained=writer_inputs["retained"],
            company_name=writer_inputs["company_name"],
            as_of=AS_OF,
        )


@pytest.mark.parametrize("configured", [False, True])
def test_issuer_receipt_absent_is_optional_but_missing_configured_file_fails(
    tmp_path, writer_inputs, configured
):
    # Given
    path = tmp_path / "absent.json" if configured else None
    # When / Then
    if configured:
        with pytest.raises(FileNotFoundError):
            load_issuer_association(
                path,
                retained=writer_inputs["retained"],
                company_name=writer_inputs["company_name"],
                as_of=AS_OF,
            )
    else:
        assert (
            load_issuer_association(
                path,
                retained=writer_inputs["retained"],
                company_name=writer_inputs["company_name"],
                as_of=AS_OF,
            )
            is None
        )


def test_writer_ambiguous_subject_rejected(writer_inputs: WriterInputs):
    def change(manifest):
        other = identity().model_copy(deep=True)
        other.candidate.candidate_id = "co-b"
        manifest.companies["co-b"] = other

    with pytest.raises(CompanyContextError, match="AMBIGUOUS_ISSUER_SUBJECT"):
        build_writer_context(**altered_store(writer_inputs, change))


def test_writer_unknown_subject_not_inferred_from_name(writer_inputs: WriterInputs):
    writer_inputs["company_name"] = "Guessed issuer"
    with pytest.raises(CompanyContextError, match="ISSUER_ASSOCIATION_REQUIRED"):
        build_writer_context(**writer_inputs)


@pytest.mark.parametrize(
    "edge", [None, "supporting_evidence_ids", "conflicts_with", "supersedes"]
)
def test_writer_foreign_subject_rejected_through_every_edge(
    writer_inputs: WriterInputs, edge
):
    def change(manifest):
        if edge == "supersedes":
            manifest.evidence["ev-a"].supersedes = "ev-b"
        elif edge is not None:
            setattr(manifest.evidence["ev-a"], edge, ["ev-b"])

    inputs = altered_store(writer_inputs, change)
    if edge is None:
        inputs["evidence_ids"] = ["ev-b"]
    with pytest.raises(CompanyContextError, match="FOREIGN_ISSUER_EVIDENCE"):
        build_writer_context(**inputs)


@pytest.mark.parametrize(
    "damage", ["locator", "chunk_subject", "record", "excerpt", "generated", "bytes"]
)
@pytest.mark.parametrize("method", ["rag", "web"])
def test_writer_misattributed_exact_lineage_rejected(
    writer_inputs: WriterInputs, damage, method
):
    if method == "web":

        def configure_web_baseline(manifest):
            item = manifest.evidence["ev-a"]
            source = manifest.sources[item.source_id]
            manifest.documents[item.source_id] = manifest.documents[
                item.source_id
            ].model_copy(update={"origin_url": source.url})
            item.locator = f"{source.url}#:~:text={quote(item.excerpt[:200])}"
            item.provenance[0].method = "web"
            item.provenance[0].chunk_id = None

        inputs = altered_store(writer_inputs, configure_web_baseline)
        build_writer_context(**inputs).verify_binding()
        record = inputs["retained"].manifest.retrieval_records["ret-a"]
        assert record.status == "ok"
        assert "src-a" in record.source_ids
        assert "ev-a" in record.evidence_ids
    else:
        inputs = writer_inputs

    def change(manifest):
        if damage == "locator":
            manifest.evidence["ev-a"].locator += "#wrong"
        elif damage == "chunk_subject":
            if method == "web":
                manifest.documents["src-a"] = manifest.documents["src-a"].model_copy(
                    update={"candidate_ids": ("co-b",)}
                )
            else:
                manifest.chunks["chunk-a"].candidate_ids = ["co-b"]
        elif damage == "record":
            manifest.retrieval_records["ret-a"].evidence_ids = []
        elif damage == "excerpt":
            manifest.evidence["ev-a"].excerpt = "Unsupported quotation"
        elif damage == "generated":
            manifest.sources["src-a"].bibliographic_metadata["generated_report"] = True
        else:
            manifest.sources["src-a"].content_hash = "sha256:forged"
            manifest.documents["src-a"] = manifest.documents["src-a"].model_copy(
                update={"content_hash": "sha256:forged"}
            )

    with pytest.raises(CompanyContextError):
        build_writer_context(**altered_store(inputs, change))


@pytest.mark.parametrize("field", ["event_date", "value_as_of", "source", "retrieval"])
def test_writer_cutoff_applies_to_original_and_support(
    writer_inputs: WriterInputs, field
):
    def change(manifest):
        manifest.evidence["ev-a"].supporting_evidence_ids = ["ev-industry"]
        if field == "source":
            manifest.sources["src-industry"].published_at = date(2026, 10, 11)
        elif field == "retrieval":
            record = manifest.retrieval_records["ret-industry"]
            record.finished_at = record.finished_at.replace(day=11)
        elif field == "value_as_of":
            item = manifest.evidence["ev-industry"]
            item.value, item.unit, item.currency = 0, "million", "USD"
            item.value_as_of = date(2026, 10, 11)
        else:
            manifest.evidence["ev-industry"].event_date = date(2026, 10, 11)

    with pytest.raises(CompanyContextError, match="CUTOFF|LOCATOR"):
        build_writer_context(**altered_store(writer_inputs, change))


@pytest.mark.parametrize("edge", ["supporting_evidence_ids", "supersedes"])
def test_writer_support_cycle_and_supersession_cycle_rejected(
    writer_inputs: WriterInputs, edge
):
    def change(manifest):
        for eid, ref in (("ev-a", "ev-industry"), ("ev-industry", "ev-a")):
            setattr(
                manifest.evidence[eid], edge, ref if edge == "supersedes" else [ref]
            )

    with pytest.raises(CompanyContextError, match="CIRCULAR"):
        build_writer_context(**altered_store(writer_inputs, change))


def test_writer_conflict_supersedes_and_derived_support_close(
    writer_inputs: WriterInputs,
):
    def change(manifest):
        item = manifest.evidence["ev-support"]
        item.evidence_kind = "derived"
        item.derivation = "Controlled calculation from the industry observation."
        item.supporting_evidence_ids = ["ev-industry"]
        item.conflicts_with = ["ev-a"]
        item.supersedes = "ev-a"
        manifest.evidence["ev-a"].conflicts_with = ["ev-support"]

    inputs = altered_store(writer_inputs, change)
    inputs["evidence_ids"] = ["ev-support"]
    context = build_writer_context(**inputs).writer_context
    assert set(context.evidence) == {"ev-a", "ev-support", "ev-industry"}
    assert context.evidence["ev-support"].supporting_evidence_ids == ["ev-industry"]
    assert context.evidence["ev-support"].supersedes == "ev-a"
    assert context.evidence["ev-a"].conflicts_with == ["ev-support"]


@pytest.mark.parametrize(
    "location", ["claim", "limitations", "derivation", "feedback", "question"]
)
@pytest.mark.parametrize(
    "text",
    [
        "https://secret.invalid/source",
        "data/local/private.pdf",
        "ret-a",
        '{"nested":{"detail":"https:\\/\\/secret.invalid\\/source"}}',
        '{"nested":{"\\u0073ource_id":"unlisted-source"}}',
    ],
)
def test_writer_metadata_rejected_in_nested_text(
    writer_inputs: WriterInputs, location, text
):
    inputs: WriterInputs = writer_inputs.copy()
    if location in ("claim", "limitations", "derivation"):

        def change(manifest):
            setattr(
                manifest.evidence["ev-a"],
                location,
                [text] if location == "limitations" else text,
            )

        inputs = altered_store(inputs, change)
    elif location == "feedback":
        inputs["may_request_information"] = False
        inputs["information_results"] = [
            InformationResult(
                schema_version="narrative-report-1",
                question_id=QUESTION_IDS[0],
                status="unavailable",
                evidence_ids=[],
                reason="acquisition_failed",
                detail=text,
            )
        ]
    else:
        inputs["questions"] = [
            q.model_copy(update={"question": text}) if n == 0 else q
            for n, q in enumerate(inputs["questions"])
        ]
    with pytest.raises(CompanyContextError, match="SOURCE_METADATA"):
        build_writer_context(**inputs)


def test_writer_hash_binds_private_registry_cutoff_and_questions(
    writer_inputs: WriterInputs,
):
    original = build_writer_context(**writer_inputs)

    def change(manifest):
        manifest.sources["src-a"].access_notes += " Additional private receipt."

    changed = build_writer_context(**altered_store(writer_inputs, change))
    assert changed.writer_context.evidence == original.writer_context.evidence
    assert changed.context_id != original.context_id
    with pytest.raises(CompanyContextError, match="BINDING_MISMATCH"):
        replace(original, citation_payload=changed.citation_payload).verify_binding()
    later_inputs = writer_inputs.copy()
    later_inputs["as_of"] = date(2026, 10, 11)
    later = build_writer_context(**later_inputs)
    questions = writer_inputs["questions"].copy()
    questions[0] = questions[0].model_copy(
        update={"question": "Different controlled question"}
    )
    revised_inputs = writer_inputs.copy()
    revised_inputs["questions"] = questions
    revised = build_writer_context(**revised_inputs)
    assert len({original.context_id, later.context_id, revised.context_id}) == 3
