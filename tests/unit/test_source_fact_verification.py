"""#201: source-backed technology.integration adjudication (synthetic PDF only).

The PDF here is generated in-test; it is not actual source truth. Registry entries
are a synthetic controller-owned review stand-in, never production review.
"""

from dataclasses import asdict, replace
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from pypdf.generic import NumberObject as Num
from tests.unit.test_technology_approved import case

from skala_rag.agents.moat_verification import (
    core_artifact_digest,
    frozen_snapshot_digest,
)
from skala_rag.rag.corpus import ManifestDocument
from skala_rag.rag.extraction import PageChunkSettings, extract_pdf
from skala_rag.rag.text_review import TextIndexReview, text_hash
from skala_rag.tools.source_fetch import content_hash

LINES = (
    "A conditions B.",
    "B sends targets to C at 50 Hz.",
    "D is an external robot platform of Synthetic System S.",
    "A is a core component of Synthetic System S.",
    "B is a core component of Synthetic System S.",
    "C is a core component of Synthetic System S.",
    "D is a component of Synthetic System S.",
    "A is a self-developed core component of Synthetic System S.",
    "B is a self-developed core component of Synthetic System S.",
    "C is a self-developed core component of Synthetic System S.",
    "D sends targets to C.",
    "Independent testing confirms integration outcome of Synthetic System S: A->B->C.",
)
SETTINGS = PageChunkSettings(
    10000, 0, "none-page-atomic", "technical_whitepaper", "synthetic-201"
)
SYSTEM = "Synthetic System S"


def make_pdf(lines):
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    image = DecodedStreamObject()
    image.set_data(b"\x00")
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): Num(1),
            NameObject("/Height"): Num(1),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): Num(8),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            ),
            NameObject("/XObject"): DictionaryObject(
                {NameObject("/Im1"): writer._add_object(image)}
            ),
        }
    )
    body = "BT /F1 10 Tf 40 750 Td 12 TL "
    body += " ".join(f"({line}) '" for line in lines) + " ET"
    stream = DecodedStreamObject()
    stream.set_data(body.encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def world(tmp_path: Path, *, independent=False, name="a", lines=LINES):
    """Synthetic snapshot + trusted local PDF; returns (snapshot, rubric, sources)."""
    from skala_rag.agents.source_fact_verification import TrustedSource

    snapshot, _, kwargs = case()
    rubric = kwargs["rubric"]
    data = make_pdf(lines)
    local = tmp_path / "data/local" / f"{name}.pdf"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)
    schema = snapshot.schema_version
    base_source = next(iter(snapshot.sources.values()))
    source = base_source.model_copy(
        update={
            "source_id": f"src-synthetic-{name}",
            "url": None,
            "local_path": f"data/local/{name}.pdf",
            "content_hash": content_hash(data),
            "language": "en",
            "title": "Synthetic 201",
        }
    )
    doc = ManifestDocument(
        schema_version=schema,
        document_id=f"doc-{name}",
        source_id=source.source_id,
        local_path=source.local_path,
        content_hash=source.content_hash,
        title="Synthetic 201",
        language="en",
        permission_note="synthetic only",
        candidate_ids=(snapshot.candidate_id,),
        scope="company",
        extraction_status="partial",
        reviewer="fixture-reviewer",
        approved=True,
    )
    first = extract_pdf(
        data,
        doc,
        source,
        corpus_version=snapshot.corpus_version,
        schema_version=schema,
        settings=SETTINGS,
        sections_by_page={},
        embedding_model="synthetic-embed",
        embedding_revision="r1",
        execution_mode="live",
    )
    review = TextIndexReview(
        schema_version=schema,
        indexing_scope="text_only",
        source_content_hash=doc.content_hash,
        approved_by="fixture-reviewer",
        approved_on="2026-09-30",
        approval_record="synthetic-approval",
        page_count=1,
        page_text_hashes={"1": text_hash(first.chunks[0].text)},
        extraction_settings=asdict(SETTINGS),
        omissions=[
            dict(
                schema_version=schema,
                code=issue.code,
                page=issue.page,
                explanation="synthetic",
            )
            for issue in first.issues
        ],
        limitations=["Synthetic only"],
    )
    doc = doc.model_copy(update={"text_index_review": review})
    chunk = first.chunks[0]
    template = snapshot.evidence["ev-fixture-eligible-technology-integration"]
    record = next(iter(snapshot.retrieval_records.values()))
    evidence = {}
    for i, line in enumerate(lines):
        eid = f"ev-synthetic-{name}-{i}"
        evidence[eid] = template.model_copy(
            update={
                "evidence_id": eid,
                "source_id": source.source_id,
                "excerpt": line,
                "provenance": [
                    p.model_copy(update={"chunk_id": chunk.chunk_id})
                    for p in template.provenance
                ],
            }
        )
    record = record.model_copy(
        update={
            "source_ids": [*record.source_ids, source.source_id],
            "chunk_ids": [*record.chunk_ids, chunk.chunk_id],
            "evidence_ids": [*record.evidence_ids, *evidence],
        }
    )
    snapshot = snapshot.model_copy(
        update={
            "sources": {**snapshot.sources, source.source_id: source},
            "chunks": {**snapshot.chunks, chunk.chunk_id: chunk},
            "evidence": {**snapshot.evidence, **evidence},
            "evidence_ids": [*snapshot.evidence_ids, *evidence],
            "retrieval_records": {record.retrieval_id: record},
        }
    )
    trusted = TrustedSource(
        path=local,
        allowed_root=tmp_path / "data/local",
        document=doc,
        source=source,
        corpus_version=snapshot.corpus_version,
        embedding_model="synthetic-embed",
        embedding_revision="r1",
        approved_chunks=(chunk,),
        independent_of_candidate=independent,
    )
    return snapshot, rubric, {source.source_id: trusted}


def sfv():
    import skala_rag.agents.source_fact_verification as module

    return module


def span(snapshot, sources, quote, *, sid=None):
    m = sfv()
    sid = sid or next(iter(sources))
    chunk = sources[sid].approved_chunks[0]
    start = chunk.text.index(quote)
    return m.SourceSpan(
        source_id=sid,
        chunk_id=chunk.chunk_id,
        page=1,
        start=start,
        end=start + len(quote),
        quote=quote,
        page_text_sha256=text_hash(chunk.text),
    )


def fact(snapshot, sources, fid, line, quote, subject, predicate, obj, **extra):
    m = sfv()
    sid = extra.pop("sid", None) or next(iter(sources))
    return m.AtomicFact(
        fact_id=fid,
        evidence_id=f"ev-synthetic-{sid.removeprefix('src-synthetic-')}-{line}",
        span=span(snapshot, sources, quote, sid=sid),
        subject=subject,
        predicate=predicate,
        object=obj,
        **extra,
    )


def base_facts(snapshot, sources):
    f = lambda *a, **k: fact(snapshot, sources, *a, **k)  # noqa: E731
    return [
        f("c-a", 3, LINES[3], SYSTEM, "component_of_system", "A"),
        f("c-b", 4, LINES[4], SYSTEM, "component_of_system", "B"),
        f("c-c", 5, LINES[5], SYSTEM, "component_of_system", "C"),
        f("e-ab", 0, LINES[0], "A", "directed_connection", "B"),
        f("e-bc", 1, LINES[1], "B", "directed_connection", "C"),
    ]


def own(snapshot, sources, comp, quote, line, predicate="component_self_developed"):
    return fact(
        snapshot, sources, f"o-{comp}-{predicate}", line, quote, comp, predicate, SYSTEM
    )


def external_d(snapshot, sources):
    return [
        fact(
            snapshot,
            sources,
            "c-d",
            6,
            LINES[6],
            SYSTEM,
            "component_of_system",
            "D",
        ),
        fact(snapshot, sources, "e-dc", 10, LINES[10], "D", "directed_connection", "C"),
        own(
            snapshot,
            sources,
            "D",
            LINES[2],
            2,
            "component_external_platform",
        ),
    ]


def artifact(snapshot, rubric, facts, *, proposer="llm", reviews=None, **changes):
    m = sfv()
    spec = rubric["dimensions"]["technology"]["criteria"]["technology.integration"]
    fields = dict(
        run_id=snapshot.run_id,
        candidate_id=snapshot.candidate_id,
        evaluation_round=snapshot.evaluation_round,
        evidence_revision=snapshot.evidence_revision,
        snapshot_sha256=frozen_snapshot_digest(snapshot),
        policy_version=snapshot.policy_version,
        rubric_sha256=core_artifact_digest(rubric),
        criterion_id="technology.integration",
        system_subject=SYSTEM,
        minimum_evidence_text=tuple(spec["minimum_evidence"]),
        anchor_texts={int(k): v for k, v in spec["anchors"].items()},
        proposer=proposer,
        facts=tuple(facts),
        reviews=tuple(
            reviews
            if reviews is not None
            else (
                m.FactReview("synthetic-reviewer-1", m.fact_digest(x), "accepted", "ok")
                for x in facts
            )
        ),
    )
    return m.FactArtifact(**(fields | changes))


def registry(art):
    m = sfv()
    return m.TrustedReviewRegistry(
        {"synthetic-reviewer-1": frozenset(r.fact_sha256 for r in art.reviews)}
    )


def adjudicate(snapshot, rubric, art, sources, reg=None):
    return sfv().adjudicate_technology_integration(
        snapshot,
        rubric,
        art,
        sources=sources,
        registry=registry(art) if reg is None else reg,
    )


# ---- source proof -------------------------------------------------------


def test_source_proof_rereads_bytes_and_matches_approved_chunk(tmp_path):
    snapshot, _, sources = world(tmp_path)
    (trusted,) = sources.values()
    proof = sfv().verify_original_source(trusted)
    assert proof.content_hash == trusted.source.content_hash
    assert set(proof.chunks) == {trusted.approved_chunks[0].chunk_id}
    sfv().verify_span(proof, span(snapshot, sources, "C"))


@pytest.mark.parametrize(
    "tamper", ["bytes", "hash", "chunk_text", "outside_root", "missing_review"]
)
def test_source_tampering_is_technical_rejection(tmp_path, tamper):
    _, _, sources = world(tmp_path)
    (trusted,) = sources.values()
    if tamper == "bytes":
        trusted.path.write_bytes(make_pdf((*LINES[:2], "Platform D is internal.")))
    elif tamper == "hash":
        trusted = replace(
            trusted,
            source=trusted.source.model_copy(
                update={"content_hash": "sha256:" + "0" * 64}
            ),
        )
    elif tamper == "chunk_text":
        chunk = trusted.approved_chunks[0]
        trusted = replace(
            trusted,
            approved_chunks=(chunk.model_copy(update={"text": chunk.text + " x"}),),
        )
    elif tamper == "outside_root":
        trusted = replace(trusted, allowed_root=tmp_path / "elsewhere")
    else:
        trusted = replace(
            trusted,
            document=trusted.document.model_copy(update={"text_index_review": None}),
        )
    with pytest.raises(sfv().SourceFactError):
        sfv().verify_original_source(trusted)


@pytest.mark.parametrize("field", ["page", "start", "end", "quote", "page_hash"])
def test_span_tampering_is_technical_rejection(tmp_path, field):
    snapshot, _, sources = world(tmp_path)
    proof = sfv().verify_original_source(next(iter(sources.values())))
    good = span(snapshot, sources, "C")
    bad = {
        "page": replace(good, page=2),
        "start": replace(good, start=good.start + 1),
        "end": replace(good, end=good.end - 1),
        "quote": replace(good, quote="controller X"),
        "page_hash": replace(good, page_text_sha256="sha256:" + "1" * 64),
    }[field]
    with pytest.raises(sfv().SourceFactError):
        sfv().verify_span(proof, bad)


# ---- semantic adjudication ----------------------------------------------


def test_minimum_evidence_supported_without_anchor_is_missing_rating(tmp_path):
    snapshot, rubric, sources = world(tmp_path)
    result = adjudicate(
        snapshot,
        rubric,
        artifact(snapshot, rubric, base_facts(snapshot, sources)),
        sources,
    )
    assert result.minimum_evidence_supported is True
    assert result.rating is None and result.status == "unsupported"


def test_rating3_requires_reviewed_self_and_external_platform(tmp_path):
    snapshot, rubric, sources = world(tmp_path)
    facts = base_facts(snapshot, sources) + external_d(snapshot, sources)
    facts.append(own(snapshot, sources, "A", LINES[7], 7))
    result = adjudicate(snapshot, rubric, artifact(snapshot, rubric, facts), sources)
    assert result.status == "supported" and result.rating == 3
    assert result.anchor_text == "핵심 일부 자체 개발 + 외부 플랫폼 통합"
    assert set(result.evidence_ids) <= set(snapshot.evidence)


def test_rating4_requires_three_reviewed_self_developed_components(tmp_path):
    snapshot, rubric, sources = world(tmp_path)
    facts = base_facts(snapshot, sources) + [
        own(snapshot, sources, "A", LINES[7], 7),
        own(snapshot, sources, "B", LINES[8], 8),
        own(snapshot, sources, "C", LINES[9], 9),
    ]
    result = adjudicate(snapshot, rubric, artifact(snapshot, rubric, facts), sources)
    assert result.rating == 4
    # Self-claims alone never reach 5, even with an asserted confirmation fact.
    facts.append(
        fact(
            snapshot,
            sources,
            "ind",
            1,
            LINES[1],
            SYSTEM,
            "independent_integration_confirmation",
            "B->C",
        )
    )
    result = adjudicate(snapshot, rubric, artifact(snapshot, rubric, facts), sources)
    assert result.rating == 4
    assert "ind" in dict(result.denied)


def test_unreviewed_llm_or_assistant_proposal_is_semantic_deny(tmp_path):
    snapshot, rubric, sources = world(tmp_path)
    facts = base_facts(snapshot, sources)
    m = sfv()
    for proposer in ("llm", "assistant"):
        art = artifact(snapshot, rubric, facts, proposer=proposer, reviews=())
        result = adjudicate(snapshot, rubric, art, sources, m.TrustedReviewRegistry({}))
        assert result.minimum_evidence_supported is False and result.rating is None
        assert {r for _, r in result.denied} == {"unreviewed"}
    # Self-issued reviews with an unregistered reference stay denied.
    art = artifact(snapshot, rubric, facts)
    forged = m.TrustedReviewRegistry({"someone-else": frozenset()})
    result = adjudicate(snapshot, rubric, art, sources, forged)
    assert result.rating is None and len(result.denied) == len(facts)


@pytest.mark.parametrize(
    "predicate,obj,measurement",
    [
        ("success_rate", "task success", ("50", "%", "performance_success_percent")),
        ("paid_contract", "customer", None),
        ("customer_long_term_stability", "fleet", None),
        ("component_self_developed_count", "3 core components", None),
        ("keyword_mentions", "AI HW SW", None),
        ("author_list", "founders", None),
        ("arxiv_publication", "third-party confirmation", None),
    ],
)
def test_same_quote_wrong_claim_source_pass_semantic_reject(
    tmp_path, predicate, obj, measurement
):
    snapshot, rubric, sources = world(tmp_path)
    m = sfv()
    proof = m.verify_original_source(next(iter(sources.values())))
    extra = {}
    if measurement:
        extra["measurement"] = m.Measurement(*measurement, conditions=None)
    wrong = fact(
        snapshot, sources, "wrong", 1, LINES[1], SYSTEM, predicate, obj, **extra
    )
    m.verify_span(proof, wrong.span)  # source proof passes
    # Even when a reviewer accepted this exact digest, the claim is not admissible.
    facts = base_facts(snapshot, sources) + [wrong]
    result = adjudicate(snapshot, rubric, artifact(snapshot, rubric, facts), sources)
    assert "wrong" in dict(result.denied)
    assert result.rating is None


def test_measurement_must_literally_match_quote(tmp_path):
    snapshot, rubric, sources = world(tmp_path)
    m = sfv()
    ok = fact(
        snapshot,
        sources,
        "hz",
        1,
        LINES[1],
        "B",
        "directed_connection",
        "C",
        measurement=m.Measurement("50", "Hz", "command_frequency", None),
    )
    bad = replace(
        ok,
        fact_id="pct",
        measurement=m.Measurement("50", "%", "command_frequency", None),
    )
    result = adjudicate(
        snapshot,
        rubric,
        artifact(snapshot, rubric, base_facts(snapshot, sources) + [ok, bad]),
        sources,
    )
    denied = dict(result.denied)
    assert "hz" not in denied and denied["pct"] == "measurement_not_in_quote"


def test_review_of_different_claim_does_not_transfer(tmp_path):
    snapshot, rubric, sources = world(tmp_path)
    facts = base_facts(snapshot, sources)
    art = artifact(snapshot, rubric, facts)
    reg = registry(art)
    swapped = replace(facts[3], object="C")  # same quote, different edge
    art2 = artifact(
        snapshot, rubric, [*facts[:3], swapped, facts[4]], reviews=art.reviews
    )
    result = adjudicate(snapshot, rubric, art2, sources, reg)
    assert dict(result.denied)[facts[3].fact_id] == "unreviewed"


@pytest.mark.parametrize(
    "change",
    ["snapshot", "rubric", "anchor", "minimum", "run", "evidence", "source_hash"],
)
def test_binding_tamper_is_technical_rejection(tmp_path, change):
    snapshot, rubric, sources = world(tmp_path)
    facts = base_facts(snapshot, sources)
    art = artifact(snapshot, rubric, facts)
    m = sfv()
    if change == "snapshot":
        art = replace(art, snapshot_sha256="0" * 64)
    elif change == "rubric":
        art = replace(art, rubric_sha256="0" * 64)
    elif change == "anchor":
        art = replace(art, anchor_texts={**art.anchor_texts, 3: "핵심 일부 자체 개발"})
    elif change == "minimum":
        art = replace(art, minimum_evidence_text=("기술 문서",))
    elif change == "run":
        art = replace(art, run_id="other-run")
    elif change == "evidence":
        art = replace(
            art, facts=(replace(facts[0], evidence_id="ev-fixture-eligible-moat-ip"),)
        )
    else:
        (sid,) = sources
        src = snapshot.sources[sid].model_copy(
            update={"content_hash": "sha256:" + "2" * 64}
        )
        snapshot = snapshot.model_copy(
            update={"sources": {**snapshot.sources, sid: src}}
        )
        art = replace(art, snapshot_sha256=frozen_snapshot_digest(snapshot))
    with pytest.raises(m.SourceFactError):
        adjudicate(snapshot, rubric, art, sources)


def test_disconnected_own_component_cannot_support_rating3(tmp_path):
    snapshot, rubric, sources = world(tmp_path)
    basics = base_facts(snapshot, sources)
    facts = basics[:3] + external_d(snapshot, sources)
    facts.append(own(snapshot, sources, "A", LINES[7], 7))
    result = adjudicate(snapshot, rubric, artifact(snapshot, rubric, facts), sources)
    assert result.minimum_evidence_supported is True
    assert result.rating is None


@pytest.mark.parametrize(
    "mutation",
    [
        "ownership_fragment",
        "wrong_subject",
        "wrong_object",
        "metric_role",
        "conditions",
    ],
)
def test_registered_allowed_claim_still_requires_source_correspondence(
    tmp_path, mutation
):
    snapshot, rubric, sources = world(tmp_path)
    m = sfv()
    good = fact(
        snapshot,
        sources,
        "claim",
        1,
        LINES[1],
        "B",
        m.CONNECTION,
        "C",
        measurement=m.Measurement("50", "Hz", "command_frequency", None),
    )
    wrong = {
        "ownership_fragment": fact(
            snapshot, sources, "claim", 0, "A", "A", m.SELF_DEVELOPED, SYSTEM
        ),
        "wrong_subject": replace(good, subject="A"),
        "wrong_object": replace(good, object="A"),
        "metric_role": replace(
            good,
            measurement=m.Measurement("50", "Hz", "performance_success_percent", None),
        ),
        "conditions": replace(
            good, measurement=m.Measurement("50", "Hz", "command_frequency", "outdoors")
        ),
    }[mutation]
    art = artifact(snapshot, rubric, [wrong])
    decision = m.assess_fact(
        wrong,
        m.verify_original_source(next(iter(sources.values()))),
        registry(art),
        art.reviews,
    )
    assert decision.source_proof == "PASS"
    assert decision.semantic == "NOT_ESTABLISHED"


@pytest.mark.parametrize(
    "sentence",
    [
        "A is not a self-developed core component of Synthetic System S.",
        "A is a self-developed core component of Other System.",
        "Other System developed A, not Synthetic System S.",
    ],
)
def test_registered_negated_or_foreign_ownership_denied(tmp_path, sentence):
    snapshot, rubric, sources = world(tmp_path, lines=(*LINES, sentence))
    wrong = own(snapshot, sources, "A", sentence, len(LINES))
    result = adjudicate(snapshot, rubric, artifact(snapshot, rubric, [wrong]), sources)
    assert wrong.fact_id in dict(result.denied)


def test_affirmative_quote_cut_from_negated_sentence_is_not_established(tmp_path):
    quote = f"A is a self-developed core component of {SYSTEM}."
    snapshot, rubric, sources = world(tmp_path, lines=(f"It is false that {quote}",))
    m = sfv()
    claim = own(snapshot, sources, "A", quote, 0)
    art = artifact(snapshot, rubric, [claim])
    proof = m.verify_original_source(next(iter(sources.values())))
    m.verify_span(proof, claim.span)
    decision = m.assess_fact(claim, proof, registry(art), art.reviews)
    assert decision.source_proof == "PASS"
    assert decision.semantic == "NOT_ESTABLISHED"
    assert decision.reason == "source_relation_not_established"
    result = adjudicate(snapshot, rubric, art, sources)
    assert dict(result.denied)[claim.fact_id] == decision.reason
    assert result.rating is None


def test_same_system_external_claim_excludes_component_from_owned_core_count(tmp_path):
    quote = f"A is an external robot platform of {SYSTEM}."
    snapshot, rubric, sources = world(tmp_path, lines=(*LINES, quote))
    facts = base_facts(snapshot, sources) + [
        own(snapshot, sources, c, LINES[i], i)
        for c, i in zip("ABC", (7, 8, 9), strict=True)
    ]
    assert (
        adjudicate(snapshot, rubric, artifact(snapshot, rubric, facts), sources).rating
        == 4
    )
    external = own(snapshot, sources, "A", quote, len(LINES), sfv().EXTERNAL)
    facts.append(external)
    result = adjudicate(snapshot, rubric, artifact(snapshot, rubric, facts), sources)
    # Both exact claims are admitted; own-minus-external leaves only B/C owned.
    assert not result.denied
    assert {facts[5].fact_id, external.fact_id} <= set(result.admitted_fact_ids)
    assert result.minimum_evidence_supported is True
    assert result.rating == 3


@pytest.mark.parametrize("mode", ["disconnected", "not_core"])
def test_rating4_requires_integrated_core_components(tmp_path, mode):
    lines = (
        LINES
        if mode == "disconnected"
        else tuple(x.replace("is a core component", "is a component") for x in LINES)
    )
    snapshot, rubric, sources = world(tmp_path, lines=lines)
    facts = [
        fact(snapshot, sources, f"c-{c}", i, lines[i], SYSTEM, "component_of_system", c)
        for c, i in zip("ABC", (3, 4, 5), strict=True)
    ]
    facts += [
        own(snapshot, sources, c, LINES[i], i)
        for c, i in zip("ABC", (7, 8, 9), strict=True)
    ]
    facts.append(
        fact(snapshot, sources, "e-ab", 0, LINES[0], "A", "directed_connection", "B")
    )
    if mode != "disconnected":
        facts.append(
            fact(
                snapshot, sources, "e-bc", 1, LINES[1], "B", "directed_connection", "C"
            )
        )
    result = adjudicate(snapshot, rubric, artifact(snapshot, rubric, facts), sources)
    assert result.minimum_evidence_supported is True
    assert result.rating is None


def merge_worlds(left, right):
    snapshot, rubric, sources = left
    other, _, other_sources = right
    record = next(iter(snapshot.retrieval_records.values()))
    other_record = next(iter(other.retrieval_records.values()))
    record = record.model_copy(
        update={
            field: list(
                dict.fromkeys([*getattr(record, field), *getattr(other_record, field)])
            )
            for field in ("source_ids", "chunk_ids", "evidence_ids")
        }
    )
    evidence = snapshot.evidence | other.evidence
    snapshot = snapshot.model_copy(
        update={
            "sources": snapshot.sources | other.sources,
            "chunks": snapshot.chunks | other.chunks,
            "evidence": evidence,
            "evidence_ids": list(evidence),
            "retrieval_records": {record.retrieval_id: record},
        }
    )
    return snapshot, rubric, sources | other_sources


@pytest.mark.parametrize(
    "mode,expected",
    [("valid", 5), ("foreign", 4), ("self_source", 4), ("disconnected", 4)],
)
def test_two_source_confirmation_is_about_this_integrated_system(
    tmp_path, mode, expected
):
    quote = LINES[11]
    if mode == "foreign":
        quote = quote.replace(SYSTEM, "Other System")
    if mode == "disconnected":
        quote = quote.replace("A->B->C", "D->C")
    snapshot, rubric, sources = merge_worlds(
        world(tmp_path),
        world(
            tmp_path,
            name="b",
            independent=mode != "self_source",
            lines=(*LINES[:11], quote),
        ),
    )
    facts = base_facts(snapshot, sources)
    facts += [
        own(snapshot, sources, c, LINES[i], i)
        for c, i in zip("ABC", (7, 8, 9), strict=True)
    ]
    facts.append(
        fact(
            snapshot,
            sources,
            "confirmation",
            11,
            quote,
            "Other System" if mode == "foreign" else SYSTEM,
            "independent_integration_confirmation",
            "D->C" if mode == "disconnected" else "A->B->C",
            sid="src-synthetic-b",
        )
    )
    result = adjudicate(snapshot, rubric, artifact(snapshot, rubric, facts), sources)
    assert result.rating == expected
    if mode == "valid":
        assert {snapshot.evidence[e].source_id for e in result.evidence_ids} == set(
            sources
        )
        from skala_rag.agents.technology_verification import (
            anchor_from_integration_adjudication,
            integration_adjudication_resolver,
            validate_technology_anchor,
        )
        from skala_rag.contracts.assessment import CriterionAssessment

        art = artifact(snapshot, rubric, facts)
        receipt = anchor_from_integration_adjudication(
            result, "synthetic-two-source", rubric=rubric
        )
        resolver = integration_adjudication_resolver(
            snapshot, rubric, art, sources=sources, registry=registry(art)
        )
        criterion = CriterionAssessment(
            schema_version=snapshot.schema_version,
            criterion_id="technology.integration",
            status="observed",
            rating=5,
            evidence_ids=list(result.evidence_ids),
            rationale="synthetic",
        )
        assert (
            validate_technology_anchor(
                receipt,
                rubric=rubric,
                snapshot=snapshot,
                criterion=criterion,
                verifier=resolver,
            )
            is True
        )
    else:
        assert "ev-synthetic-b-11" not in result.evidence_ids


@pytest.mark.parametrize(
    "field,value",
    [
        ("author", "Other Author"),
        ("publisher", "Other Publisher"),
        ("published_at", "2020-01-01"),
        ("access_notes", "different"),
        ("bibliographic_metadata", {"edition": "other"}),
    ],
)
def test_same_bytes_changed_snapshot_source_metadata_rejected(tmp_path, field, value):
    snapshot, rubric, sources = world(tmp_path)
    sid = next(iter(sources))
    payload = snapshot.sources[sid].model_dump()
    payload[field] = value
    from skala_rag.contracts import Source

    changed = Source.model_validate(payload, context={"execution_mode": "fixture"})
    snapshot = snapshot.model_copy(
        update={"sources": snapshot.sources | {sid: changed}}
    )
    art = artifact(snapshot, rubric, base_facts(snapshot, sources))
    with pytest.raises(sfv().SourceFactError):
        adjudicate(snapshot, rubric, art, sources)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_id", "different"),
        ("local_path", "data/local/other.pdf"),
        ("language", "ko"),
        ("title", "different"),
    ],
)
def test_document_source_identity_mismatch_rejected(tmp_path, field, value):
    _, _, sources = world(tmp_path)
    trusted = next(iter(sources.values()))
    trusted = replace(
        trusted, document=trusted.document.model_copy(update={field: value})
    )
    with pytest.raises(sfv().SourceFactError):
        sfv().verify_original_source(trusted)


@pytest.mark.parametrize("mode", ["source_cutoff", "evidence_cutoff", "corpus"])
def test_cutoff_and_corpus_are_not_just_bytes_digest(tmp_path, mode):
    from datetime import date, datetime, timezone

    snapshot, rubric, sources = world(tmp_path)
    sid = next(iter(sources))
    trusted = sources[sid]
    if mode == "source_cutoff":
        src = trusted.source.model_copy(
            update={"retrieved_at": datetime(2099, 1, 1, tzinfo=timezone.utc)}
        )
        sources[sid] = replace(trusted, source=src)
        snapshot = snapshot.model_copy(
            update={"sources": snapshot.sources | {sid: src}}
        )
    elif mode == "evidence_cutoff":
        eid = "ev-synthetic-a-3"
        ev = snapshot.evidence[eid].model_copy(update={"event_date": date(2099, 1, 1)})
        snapshot = snapshot.model_copy(
            update={"evidence": snapshot.evidence | {eid: ev}}
        )
    else:
        sources[sid] = replace(trusted, corpus_version="other")
    art = artifact(snapshot, rubric, base_facts(snapshot, sources))
    with pytest.raises(sfv().SourceFactError):
        adjudicate(snapshot, rubric, art, sources)


@pytest.mark.parametrize(
    "mutation",
    [
        "round_bool",
        "revision_bool",
        "facts_list",
        "reviews_list",
        "review_object",
        "review_decision",
        "anchors_bool",
        "proposer",
        "fact_subject",
        "span_page",
        "span_quote",
        "measurement",
        "limitations",
        "registry",
        "registry_values",
        "proof_chunk",
    ],
)
def test_malformed_nested_dataclasses_are_technical_rejections(tmp_path, mutation):
    snapshot, rubric, sources = world(tmp_path)
    m = sfv()
    facts = base_facts(snapshot, sources)
    art = artifact(snapshot, rubric, facts)
    reg = registry(art)
    changes = {
        "round_bool": {"evaluation_round": True},
        "revision_bool": {"evidence_revision": False},
        "facts_list": {"facts": list(art.facts)},
        "reviews_list": {"reviews": list(art.reviews)},
        "review_object": {"reviews": (object(),)},
        "review_decision": {"reviews": (replace(art.reviews[0], decision="maybe"),)},
        "anchors_bool": {"anchor_texts": {True: "bad"}},
        "proposer": {"proposer": "robot"},
        "fact_subject": {"facts": (replace(facts[0], subject=123),)},
        "span_page": {
            "facts": (replace(facts[0], span=replace(facts[0].span, page=True)),)
        },
        "span_quote": {
            "facts": (replace(facts[0], span=replace(facts[0].span, quote=["A"])),)
        },
        "measurement": {
            "facts": (
                replace(
                    facts[0],
                    measurement=m.Measurement(50, "Hz", "command_frequency", None),
                ),
            )
        },
        "limitations": {"facts": (replace(facts[0], limitations=["bad"]),)},
    }.get(mutation, {})
    art = replace(art, **changes)
    if mutation == "registry":
        reg = m.TrustedReviewRegistry([])
    elif mutation == "registry_values":
        reg = m.TrustedReviewRegistry(
            {"synthetic-reviewer-1": list(reg.accepted["synthetic-reviewer-1"])}
        )
    with pytest.raises(m.SourceFactError):
        if mutation == "proof_chunk":
            proof = m.verify_original_source(sources[next(iter(sources))])
            m.assess_fact(
                facts[0],
                replace(proof, chunks={facts[0].span.chunk_id: object()}),
                reg,
                art.reviews,
            )
        else:
            adjudicate(snapshot, rubric, art, sources, reg)


@pytest.mark.parametrize(
    "rubric",
    [{}, {"dimensions": {"technology": {"criteria": {"technology.integration": 1}}}}],
)
def test_malformed_rubric_is_redacted_technical_error(tmp_path, rubric):
    snapshot, valid, sources = world(tmp_path)
    art = artifact(snapshot, valid, base_facts(snapshot, sources))
    with pytest.raises(sfv().SourceFactError):
        adjudicate(snapshot, rubric, art, sources)


def test_non_json_rubric_payload_is_redacted_technical_error(tmp_path):
    snapshot, rubric, sources = world(tmp_path)
    art = artifact(snapshot, rubric, base_facts(snapshot, sources))
    malformed = {**rubric, "synthetic-secret-payload": object()}
    with pytest.raises(sfv().SourceFactError, match="^RUBRIC_REJECTED$") as caught:
        adjudicate(snapshot, malformed, art, sources)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    assert "synthetic-secret-payload" not in str(caught.value)


def test_full_original_snapshot_non_target_fact_is_bound(tmp_path):
    snapshot, rubric, sources = world(tmp_path)
    art = artifact(snapshot, rubric, base_facts(snapshot, sources))
    snapshot.evidence["ev-fixture-eligible-moat-ip"].claim = "other non-target fact"
    with pytest.raises(sfv().SourceFactError, match="BINDING_REJECTED"):
        adjudicate(snapshot, rubric, art, sources)


def test_source_proof_does_not_share_chunk_aliases(tmp_path):
    _, _, sources = world(tmp_path)
    trusted = next(iter(sources.values()))
    proof = sfv().verify_original_source(trusted)
    cid = trusted.approved_chunks[0].chunk_id
    proof.chunks[cid].candidate_ids.clear()
    assert trusted.approved_chunks[0].candidate_ids


# ---- concrete resolver for the existing anchor validator ----------------


def test_resolver_accepts_only_exact_adjudication(tmp_path):
    from skala_rag.agents.technology_verification import (
        anchor_from_integration_adjudication,
        integration_adjudication_resolver,
        validate_technology_anchor,
    )

    snapshot, rubric, sources = world(tmp_path)
    facts = base_facts(snapshot, sources) + external_d(snapshot, sources)
    facts.append(own(snapshot, sources, "A", LINES[7], 7))
    result = adjudicate(snapshot, rubric, artifact(snapshot, rubric, facts), sources)
    from skala_rag.contracts.assessment import CriterionAssessment

    criterion = CriterionAssessment(
        schema_version=snapshot.schema_version,
        criterion_id="technology.integration",
        status="observed",
        rating=3,
        evidence_ids=list(result.evidence_ids),
        rationale="synthetic",
    )
    receipt = anchor_from_integration_adjudication(
        result, "synthetic-review:201", rubric=rubric
    )
    resolver = integration_adjudication_resolver(
        snapshot,
        rubric,
        artifact(snapshot, rubric, facts),
        sources=sources,
        registry=registry(artifact(snapshot, rubric, facts)),
    )
    kwargs = dict(
        rubric=rubric, snapshot=snapshot, criterion=criterion, verifier=resolver
    )
    assert validate_technology_anchor(receipt, **kwargs) is True
    assert resolver(replace(receipt, rating=4)) is False
    assert resolver(replace(receipt, evidence_ids=receipt.evidence_ids[:1])) is False
    assert resolver(replace(receipt, snapshot_sha256="0" * 64)) is False
    assert resolver(replace(receipt, anchor_facts_reviewed=False)) is False
    assert resolver(object()) is False


@pytest.mark.parametrize(
    "change",
    [
        "rating2",
        "wrong_anchor",
        "wrong_rubric_digest",
        "rating6",
        "minimum",
        "anchor",
        "digest",
        "evidence_list",
        "evidence_duplicate",
        "admitted_empty",
        "denied_overlap",
    ],
)
def test_incoherent_public_adjudication_is_not_a_receipt(tmp_path, change):
    from skala_rag.agents.technology_verification import (
        TechnologyReviewError,
        anchor_from_integration_adjudication,
    )

    snapshot, rubric, sources = world(tmp_path)
    facts = (
        base_facts(snapshot, sources)
        + external_d(snapshot, sources)
        + [own(snapshot, sources, "A", LINES[7], 7)]
    )
    result = adjudicate(snapshot, rubric, artifact(snapshot, rubric, facts), sources)
    changes = {
        "rating2": {"rating": 2},
        "wrong_anchor": {"anchor_text": "another approved anchor"},
        "wrong_rubric_digest": {"rubric_sha256": "0" * 64},
        "rating6": {"rating": 6},
        "minimum": {"minimum_evidence_supported": False},
        "anchor": {"anchor_text": ""},
        "digest": {"snapshot_sha256": "not-a-digest"},
        "evidence_list": {"evidence_ids": list(result.evidence_ids)},
        "evidence_duplicate": {
            "evidence_ids": (*result.evidence_ids, result.evidence_ids[0])
        },
        "admitted_empty": {"admitted_fact_ids": ()},
        "denied_overlap": {"denied": ((result.admitted_fact_ids[0], "unreviewed"),)},
    }
    with pytest.raises(TechnologyReviewError):
        anchor_from_integration_adjudication(
            replace(result, **changes[change]), "synthetic", rubric=rubric
        )


def consumer_case(tmp_path):
    from skala_rag.agents.technology_verification import (
        anchor_from_integration_adjudication,
    )
    from skala_rag.contracts.assessment import CriterionAssessment

    snapshot, rubric, sources = world(tmp_path)
    facts = (
        base_facts(snapshot, sources)
        + external_d(snapshot, sources)
        + [own(snapshot, sources, "A", LINES[7], 7)]
    )
    art = artifact(snapshot, rubric, facts)
    reg = registry(art)
    result = adjudicate(snapshot, rubric, art, sources, reg)
    receipt = anchor_from_integration_adjudication(result, "synthetic", rubric=rubric)
    criterion = CriterionAssessment(
        schema_version=snapshot.schema_version,
        criterion_id=receipt.criterion_id,
        status="observed",
        rating=receipt.rating,
        evidence_ids=list(receipt.evidence_ids),
        rationale="synthetic",
    )
    return snapshot, rubric, sources, art, reg, result, receipt, criterion


@pytest.mark.parametrize(
    "mutation", ["registry", "rubric", "source", "snapshot", "artifact", "nested_fact"]
)
def test_resolver_captures_original_controller_inputs(tmp_path, mutation):
    from skala_rag.agents.technology_verification import (
        integration_adjudication_resolver,
        validate_technology_anchor,
    )

    snapshot, rubric, sources, art, reg, _, receipt, criterion = consumer_case(tmp_path)
    resolver = integration_adjudication_resolver(
        snapshot, rubric, art, sources=sources, registry=reg
    )
    original_snapshot = snapshot.model_copy(deep=True)
    from copy import deepcopy

    original_rubric = deepcopy(rubric)
    if mutation == "registry":
        reg.accepted.clear()
    elif mutation == "rubric":
        rubric["dimensions"]["technology"]["criteria"]["technology.integration"][
            "anchors"
        ][3] = "forged"
    elif mutation == "source":
        sources[next(iter(sources))].source.bibliographic_metadata["edition"] = "forged"
        sources.clear()
    elif mutation == "snapshot":
        eid = "ev-fixture-eligible-moat-ip"
        snapshot.evidence[eid].claim = "changed unrelated criterion"
    elif mutation == "nested_fact":
        object.__setattr__(art.facts[0], "subject", "changed original")
    else:
        art.anchor_texts.clear()
    assert (
        validate_technology_anchor(
            receipt,
            rubric=original_rubric,
            snapshot=original_snapshot,
            criterion=criterion,
            verifier=resolver,
        )
        is True
    )


def test_forged_coherent_rating5_result_rejected_by_real_consumer(tmp_path):
    from skala_rag.agents.technology_verification import (
        anchor_from_integration_adjudication,
        integration_adjudication_resolver,
        validate_technology_anchor,
    )

    snapshot, rubric, sources, art, reg, result, _, criterion = consumer_case(tmp_path)
    resolver = integration_adjudication_resolver(
        snapshot, rubric, art, sources=sources, registry=reg
    )
    forged = replace(result, rating=5, anchor_text=art.anchor_texts[5])
    receipt = anchor_from_integration_adjudication(forged, "synthetic", rubric=rubric)
    criterion = criterion.model_copy(update={"rating": 5})
    assert (
        validate_technology_anchor(
            receipt,
            rubric=rubric,
            snapshot=snapshot,
            criterion=criterion,
            verifier=resolver,
        )
        is False
    )


def test_non_target_snapshot_replay_rejected_by_real_consumer(tmp_path):
    from skala_rag.agents.technology_verification import (
        integration_adjudication_resolver,
        validate_technology_anchor,
    )

    snapshot, rubric, sources, art, reg, _, receipt, criterion = consumer_case(tmp_path)
    resolver = integration_adjudication_resolver(
        snapshot, rubric, art, sources=sources, registry=reg
    )
    eid = "ev-fixture-eligible-moat-ip"
    snapshot.evidence[eid].claim = "different non-target fact"
    rebound = replace(receipt, snapshot_sha256=frozen_snapshot_digest(snapshot))
    assert (
        validate_technology_anchor(
            receipt,
            rubric=rubric,
            snapshot=snapshot,
            criterion=criterion,
            verifier=resolver,
        )
        is False
    )
    assert (
        validate_technology_anchor(
            rebound,
            rubric=rubric,
            snapshot=snapshot,
            criterion=criterion,
            verifier=resolver,
        )
        is False
    )


def test_resolver_rereads_real_source_on_validation(tmp_path):
    from skala_rag.agents.technology_verification import (
        TechnologyReviewError,
        integration_adjudication_resolver,
        validate_technology_anchor,
    )

    snapshot, rubric, sources, art, reg, _, receipt, criterion = consumer_case(tmp_path)
    resolver = integration_adjudication_resolver(
        snapshot, rubric, art, sources=sources, registry=reg
    )
    sources[next(iter(sources))].path.write_bytes(b"tampered")
    with pytest.raises(TechnologyReviewError, match="^TECHNOLOGY_REVIEW_REJECTED$"):
        validate_technology_anchor(
            receipt,
            rubric=rubric,
            snapshot=snapshot,
            criterion=criterion,
            verifier=resolver,
        )


def test_resolver_cannot_accept_a_public_result_without_controller_inputs(tmp_path):
    from skala_rag.agents.technology_verification import (
        integration_adjudication_resolver,
    )

    *_, result, receipt, criterion = consumer_case(tmp_path)
    with pytest.raises((TypeError, ValueError)):
        integration_adjudication_resolver(result)


def test_no_receipt_for_missing_rating(tmp_path):
    from skala_rag.agents.technology_verification import (
        TechnologyReviewError,
        anchor_from_integration_adjudication,
        integration_adjudication_resolver,
    )

    snapshot, rubric, sources = world(tmp_path)
    result = adjudicate(
        snapshot,
        rubric,
        artifact(snapshot, rubric, base_facts(snapshot, sources)),
        sources,
    )
    with pytest.raises(TechnologyReviewError):
        anchor_from_integration_adjudication(
            result, "synthetic-review:201", rubric=rubric
        )
    with pytest.raises(TechnologyReviewError):
        integration_adjudication_resolver(
            snapshot,
            rubric,
            artifact(snapshot, rubric, base_facts(snapshot, sources)),
            sources=sources,
            registry=registry(
                artifact(snapshot, rubric, base_facts(snapshot, sources))
            ),
        )
