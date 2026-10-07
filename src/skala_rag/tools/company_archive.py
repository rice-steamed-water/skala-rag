"""Read-only archive to NEW local CompanyResearch composition, not HTTP replay."""

import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from skala_rag.contracts import Candidate, RunInput, Source, ToolBudget, ToolResult
from skala_rag.contracts.interfaces import Clock
from skala_rag.contracts.tools import CompanyResearchBundle
from skala_rag.tools.company_research import (
    CallBudget,
    LiveResearchCompany,
    ProviderOutcome,
)

_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_REUSED_PDF_BYTES = 32 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MANIFEST_KINDS = {
    "manifest.json": "initial_public_company_research_collection_NOT_RunManifest",
    "followup-manifest.json": "supplemental_collection_NOT_RunManifest",
    "current-round-conflict-manifest.json": (
        "financing_conflict_capture_NOT_RunManifest"
    ),
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _digest(value):
    _require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value),
        "invalid SHA-256",
    )
    return value


def _identifier(value):
    _require(
        isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value),
        "invalid literal archive ID",
    )
    return value


def _path(value):
    _require(
        isinstance(value, str) and value and "\\" not in value and "\x00" not in value,
        "invalid archive path",
    )
    parts = value.split("/")
    _require(
        not PurePosixPath(value).is_absolute()
        and all(p not in ("", ".", "..") for p in parts),
        "noncanonical archive path",
    )
    return parts


def _read(root, relative):
    """Open each directory component without following symlinks; bound reads."""
    parts = _path(relative)
    limit = (
        _MAX_REUSED_PDF_BYTES
        if relative.startswith("raw/") and relative.endswith(".pdf")
        else _MAX_FILE_BYTES
    )
    fd = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in (*root.parts[1:], *parts[:-1]):
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = next_fd
        file_fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
        )
        with os.fdopen(file_fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            _require(
                stat.S_ISREG(info.st_mode) and info.st_size <= limit,
                "archive file type/size bound",
            )
            data = handle.read(limit + 1)
            _require(len(data) <= limit, "archive file size bound")
            return data
    finally:
        os.close(fd)


def _timestamp(value):
    _require(
        isinstance(value, str) and "T" in value, "explicit archive timestamp required"
    )
    moment = datetime.fromisoformat(value)
    _require(
        moment.tzinfo is not None and moment.utcoffset() is not None,
        "archive timezone required",
    )
    return moment


def _date(value):
    _require(
        isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value),
        "archive ISO date required",
    )
    return date.fromisoformat(value)


def _url(value):
    _require(isinstance(value, str), "archive URL required")
    parsed = urlsplit(value)
    _require(
        parsed.scheme == "https"
        and parsed.hostname
        and not parsed.username
        and not parsed.password,
        "public HTTPS archive URL required",
    )


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result and key.strip(), "duplicate/blank JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("nonfinite archive JSON")

    payload = json.loads(data, object_pairs_hook=pairs, parse_constant=reject_constant)

    def check(value, depth=0):
        _require(depth <= 32, "archive JSON nesting bound")
        if isinstance(value, float):
            _require(math.isfinite(value), "nonfinite archive JSON")
        elif isinstance(value, dict):
            # This archival format has no historical project generation. Do not
            # let metadata impersonate a project DTO or trip consumer closure.
            _require(
                not {
                    "schema_version",
                    "run_id",
                    "candidate_id",
                    "policy_version",
                    "corpus_version",
                    "as_of",
                }.intersection(value),
                "archive has unsupported project generation",
            )
            for key, child in value.items():
                if child is not None and key in {
                    "started_at",
                    "finished_at",
                    "sealed_at",
                    "retrieved_at",
                    "copied_at",
                    "failure_recorded_at",
                    "observed_at",
                }:
                    _timestamp(child)
                if child is not None and key in {
                    "research_as_of",
                    "date_only",
                    "event_date",
                    "publication_date_observed",
                    "publication_date_from_index",
                    "closing_date",
                    "metric_value_as_of",
                }:
                    _date(child)
                if key == "published_at" and child is not None:
                    (_timestamp if "T" in child else _date)(child)
                check(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                check(child, depth + 1)

    check(payload)
    _require(isinstance(payload, dict), "archive JSON object required")
    return payload


class _ArchiveText(HTMLParser):
    """Deterministic retained-text check, not a facts/eligibility extractor.

    Mirrors the archived HTMLParser text algorithm without loading or executing
    archive Python. No policy, observations, browser render, or model inference.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.lines = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        if (
            tag in ("p", "h1", "h2", "h3", "li", "div", "section", "br", "tr")
            and not self.skip
        ):
            self.lines.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1
        if (
            tag in ("p", "h1", "h2", "h3", "li", "div", "section", "tr")
            and not self.skip
        ):
            self.lines.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.lines.append(data)

    def text(self):
        return (
            "\n".join(
                " ".join(s.split())
                for s in "".join(self.lines).splitlines()
                if s.strip()
            )
            + "\n"
        )


def _load_archive(root, expected_pin):
    raw_index = _read(root, "collection-index.json")
    _require(_hash(raw_index) == expected_pin, "external index pin mismatch")
    index = _json(raw_index)
    _require(
        index["artifact_kind"]
        == "local_company_collection_integrity_index_NOT_approved_RunManifest",
        "unsupported archive index kind",
    )
    sealed_at = _timestamp(index["sealed_at"])
    hashes = index["file_sha256"]
    _require(
        isinstance(hashes, dict) and 0 < len(hashes) <= 128, "archive file count bound"
    )
    files = {}
    total = 0
    for path, digest in hashes.items():
        _require(path != "collection-index.json", "index cannot seal itself")
        raw = _read(root, path)
        total += len(raw)
        _require(total <= _MAX_ARCHIVE_BYTES, "archive byte bound")
        _require(_hash(raw) == _digest(digest), "archive file hash mismatch")
        files[path] = raw
    # Parse ALL sealed JSON before composition, including unselected candidates.
    payloads = {p: _json(raw) for p, raw in files.items() if p.endswith(".json")}
    manifests = {}
    manifest_paths = index["source_manifest_paths"]
    _require(
        isinstance(manifest_paths, list)
        and len(manifest_paths) == len(set(manifest_paths))
        and "manifest.json" in manifest_paths,
        "manifest path closure",
    )
    _require(
        set(manifest_paths) == set(payloads).intersection(_MANIFEST_KINDS),
        "sealed manifest closure mismatch",
    )
    rows = {}
    texts = {}
    reused = []
    scope = payloads["manifest.json"]["candidate_scope"]
    _require(
        isinstance(scope, list) and scope and len(scope) == len(set(scope)),
        "candidate scope closure",
    )
    for cid in scope:
        _identifier(cid)
    for path in manifest_paths:
        manifest = payloads[path]
        _require(
            manifest["artifact_kind"] == _MANIFEST_KINDS[path],
            "unsupported manifest kind",
        )
        if path != "manifest.json":
            _require(
                manifest["base_manifest_sha256"] == hashes["manifest.json"],
                "base manifest hash mismatch",
            )
        records = manifest["records"]
        _require(isinstance(records, list), "manifest records required")
        count_key = (
            "capture_get_attempts"
            if path == "manifest.json"
            else "additional_source_get_attempts"
        )
        _require(
            type(manifest[count_key]) is int and manifest[count_key] == len(records),
            "manifest attempt count mismatch",
        )
        manifests[path] = manifest
        if "preliminary_failure_before_batch" in manifest:
            preliminary_receipt = manifest["preliminary_failure_before_batch"]
            _require(
                isinstance(preliminary_receipt, dict), "preliminary receipt required"
            )
            _url(preliminary_receipt["url"])
            _date(preliminary_receipt["date_only"])
            _require(
                type(preliminary_receipt["http_status"]) is int
                and preliminary_receipt["http_status"] >= 400
                and preliminary_receipt["body_captured"] is False,
                "preliminary failure receipt mismatch",
            )
        for row in records:
            sid = _identifier(row["source_id"])
            _require(
                sid not in rows and row["candidate"] in scope,
                "source ID/candidate closure mismatch",
            )
            _require(
                payloads[f"receipts/{sid}.json"] == row, "manifest/receipt mismatch"
            )
            _url(row["requested_url"])
            _require(
                row["method"] == "public_https_get"
                and row["status"] in ("captured", "fetch_failed"),
                "unsupported source acquisition",
            )
            start, finish = row["started_at"], row["finished_at"]
            if start is not None and finish is not None:
                _require(
                    _timestamp(start) <= _timestamp(finish),
                    "reversed acquisition timestamps",
                )
            if finish is not None:
                _require(
                    _timestamp(finish) <= sealed_at, "acquisition after archive sealing"
                )
            rows[sid] = row
            if row["status"] == "fetch_failed":
                _require(
                    isinstance(row["failure"], str) and row["failure"].strip(),
                    "failed receipt needs failure",
                )
                _require(
                    not {"raw_path", "extracted_path"}.intersection(row),
                    "failed source cannot carry successful capture",
                )
                continue
            _timestamp(start)
            _timestamp(finish)
            _require(
                isinstance(row.get("title"), str)
                and row["title"].strip()
                and "published_at" in row,
                "captured title and explicit publication null/date required",
            )
            _url(row["resolved_url"])
            _require(
                type(row["http_status"]) is int and 200 <= row["http_status"] < 300,
                "captured HTTP status mismatch",
            )
            _require(
                row["raw_path"] == f"raw/{sid}.html"
                and row["extracted_path"] == f"text/{sid}.txt",
                "source path attribution mismatch",
            )
            raw, text_bytes = files[row["raw_path"]], files[row["extracted_path"]]
            _require(
                _hash(raw) == _digest(row["raw_sha256"])
                and _hash(text_bytes) == _digest(row["extracted_sha256"]),
                "receipt bytes/hash mismatch",
            )
            text = text_bytes.decode("utf-8")
            _require(
                type(row["raw_bytes"]) is int
                and row["raw_bytes"] == len(raw)
                and type(row["extracted_characters"]) is int
                and row["extracted_characters"] == len(text),
                "receipt size mismatch",
            )
            reader = _ArchiveText()
            reader.feed(raw.decode(row["charset"], errors="replace"))
            _require(reader.text() == text, "raw/text extraction mismatch")
            texts[sid] = text
        for asset in manifest.get("reused_assets", []):
            sid = _identifier(asset["source_id"])
            _path(asset["raw_path"])
            _require(
                sid not in rows
                and sid not in {a["source_id"] for a in reused}
                and asset["candidate"] in scope,
                "reused asset attribution mismatch",
            )
            _require(
                asset["method"] == "local_preexisting_approved_pdf_reuse"
                and asset["raw_path"].startswith("raw/")
                and asset["raw_path"].endswith(".pdf"),
                "unsupported reused asset",
            )
            _require(
                _hash(files[asset["raw_path"]]) == _digest(asset["raw_sha256"]),
                "reused asset hash mismatch",
            )
            _require(
                _timestamp(asset["copied_at"]) <= sealed_at,
                "asset copied after archive sealing",
            )
            reused.append(asset)
    _require(
        set(rows).isdisjoint({asset["source_id"] for asset in reused}),
        "captured/reused source identity collision",
    )
    _require(
        {p for p in payloads if p.startswith("receipts/")}
        == {f"receipts/{sid}.json" for sid in rows},
        "sealed receipt closure mismatch",
    )
    _require(
        {p for p in files if p.startswith(("raw/", "text/"))}
        == {
            r[k]
            for r in rows.values()
            if r["status"] == "captured"
            for k in ("raw_path", "extracted_path")
        }
        | {a["raw_path"] for a in reused},
        "raw/text file attribution closure mismatch",
    )
    packets = {}
    claim_ids = set()
    packet_paths = index["candidate_packet_paths"]
    _require(
        isinstance(packet_paths, list) and len(packet_paths) == len(set(packet_paths)),
        "packet path closure",
    )
    for path in packet_paths:
        packet = payloads[path]
        cid = _identifier(packet["candidate_label"])
        _require(
            cid in scope and cid not in packets and path == f"claims/{cid}.json",
            "packet candidate/path mismatch",
        )
        _require(
            packet["artifact_kind"]
            == "source_bound_research_packet_NOT_CompanyResearchBundle_or_Evaluation"
            and packet["research_as_of"] == payloads["manifest.json"]["research_as_of"],
            "packet kind/date mismatch",
        )
        _require(
            packet["eligibility_status"] == "unknown"
            and all(
                packet[key] is False
                for key in (
                    "scores_computed",
                    "final_allowed",
                    "na_rules_applied",
                    "approved_corpus_ingestion_performed",
                    "approved_embedding_or_llm_changed",
                )
            ),
            "packet must remain unreviewed sources-only",
        )
        _require(isinstance(packet["claims"], list), "packet claims required")
        for claim in packet["claims"]:
            claim_id = _identifier(claim["claim_id"])
            _require(claim_id not in claim_ids, "duplicate claim ID")
            claim_ids.add(claim_id)
            row = rows[claim["source_id"]]
            _require(
                row["status"] == "captured"
                and row["candidate"] == claim["candidate"] == cid,
                "claim/source candidate mismatch",
            )
            _require(
                claim["source_url"] == row["resolved_url"]
                and claim["source_class"] == row["source_class"],
                "claim/source attribution mismatch",
            )
            _require(
                claim["raw_sha256"] == row["raw_sha256"]
                and claim["extracted_sha256"] == row["extracted_sha256"],
                "claim/source hash mismatch",
            )
            _require(
                _timestamp(claim["retrieved_at"]) == _timestamp(row["finished_at"]),
                "claim acquisition moment mismatch",
            )
            _require(
                claim["criterion_rating"] is None
                and claim["review_authority_status"]
                == "unreviewed_for_production_semantics",
                "claim not unreviewed",
            )
            if "publication_date_source_id" in claim:
                date_source = rows[claim["publication_date_source_id"]]
                _require(
                    date_source["candidate"] == cid
                    and date_source["status"] == "captured",
                    "claim publication source closure",
                )
            anchor = claim["anchor"]
            text = texts[row["source_id"]]
            _require(
                anchor["unit"] == "unicode_character_offset_zero_based"
                and type(anchor["start"]) is int
                and type(anchor["end"]) is int
                and 0 <= anchor["start"] < anchor["end"] <= len(text),
                "invalid claim anchor",
            )
            _require(
                _hash(text[anchor["start"] : anchor["end"]].encode())
                == _digest(anchor["sha256_utf8"]),
                "claim anchor hash mismatch",
            )
        packets[cid] = packet
    _require(
        set(packets) == set(scope)
        and {p for p in payloads if p.startswith("claims/")} == set(packet_paths),
        "candidate packet closure mismatch",
    )
    search = payloads["source-crosscheck.json"]
    _require(
        isinstance(search["queries"], list)
        and isinstance(search["results"], list)
        and len(search["queries"])
        == len(search["results"])
        == search["search_query_count_in_this_batch"],
        "search receipt count mismatch",
    )
    _require(
        [r["query"] for r in search["results"]] == search["queries"],
        "search query closure mismatch",
    )
    preliminary = sum(
        "preliminary_failure_before_batch" in m for m in manifests.values()
    )
    failed = sum(r["status"] == "fetch_failed" for r in rows.values())
    counts = dict(
        source_get_attempts_in_batches=len(rows),
        preliminary_source_get_attempts=preliminary,
        source_get_attempts_including_preliminary=len(rows) + preliminary,
        captured_html=len(texts),
        failed_source_gets_in_batches=failed,
        failed_source_gets_including_preliminary=failed + preliminary,
        reused_existing_pdfs=len(reused),
        candidate_packets=len(packets),
        source_bound_claims=len(claim_ids),
        assistant_search_queries=len(search["queries"]),
    )
    _require(
        all(
            type(index["counts"][k]) is int and index["counts"][k] == n
            for k, n in counts.items()
        ),
        "archive aggregate count mismatch",
    )
    _require(
        index["physical_http_request_total"] is None,
        "historical physical HTTP total unsupported",
    )
    return index, manifests, packets, rows, search


@dataclass
class _ArchiveSources:
    sources: tuple[Source, ...]
    name: str = "archive-local-sources"
    required: bool = True

    def __call__(self, candidate: Candidate, calls: CallBudget) -> ProviderOutcome:
        return ProviderOutcome(
            status="ok" if self.sources else "empty", sources=self.sources
        )


def compose_archive_company_research(
    *,
    archive_root: Path,
    expected_index_sha256: str,
    candidate: Candidate,
    run_input: RunInput,
    run_id: str,
    budget: ToolBudget,
    clock: Clock,
) -> ToolResult[CompanyResearchBundle]:
    """Compose sources only; claims are unreviewed metadata, never observations."""
    _digest(expected_index_sha256)
    _require(
        isinstance(run_id, str) and run_id.strip(), "new composition run ID required"
    )
    run_input = RunInput.model_validate_json(run_input.model_dump_json())
    candidate = Candidate.model_validate_json(
        candidate.model_dump_json(), context={"execution_mode": "live"}
    )
    budget = ToolBudget.model_validate_json(budget.model_dump_json())
    _require(
        run_input.execution_mode == "live"
        and candidate.schema_version
        == run_input.schema_version
        == budget.schema_version,
        "new composition mode/schema mismatch",
    )
    _require(
        budget.max_calls >= 1 and budget.max_retries == 0,
        "finite source-only budget with zero retries required",
    )
    root = archive_root.absolute()
    try:
        index, manifests, packets, rows, search = _load_archive(
            root, expected_index_sha256
        )
        _require(
            clock.now().utcoffset() is not None
            and clock.now() >= _timestamp(index["sealed_at"]),
            "new composition clock must not predate archive sealing",
        )
        _require(
            candidate.candidate_id in packets,
            "caller candidate absent from sealed archive",
        )
        packet = packets[candidate.candidate_id]
    except (
        OSError,
        KeyError,
        TypeError,
        UnicodeError,
        LookupError,
        RecursionError,
    ) as exc:
        raise ValueError("invalid archive file/JSON closure") from exc
    sources = []
    # Validate every captured Source DTO before any composer invocation, even
    # rows for a candidate the caller did not select. The validated byte/JSON
    # snapshots are local; no path is reopened after integrity checks.
    for row in rows.values():
        if row["status"] != "captured":
            continue
        original_packet = packets[row["candidate"]]
        source = Source(
            schema_version=run_input.schema_version,
            source_id=row["source_id"],
            title=row["title"],
            publisher=row.get("publisher"),
            source_kind="web",
            url=row["resolved_url"],
            local_path=None,
            published_at=row["published_at"],
            retrieved_at=row["finished_at"],
            content_hash="sha256:" + row["raw_sha256"],
            language=row.get("language", "unknown"),
            access_notes="Retained HTML; local transformation; semantics unreviewed.",
            bibliographic_metadata=dict(
                original_receipt=row,
                original_claims=[
                    c
                    for c in original_packet["claims"]
                    if c["source_id"] == row["source_id"]
                ],
            ),
        )
        if row["candidate"] == candidate.candidate_id:
            sources.append(source)
    tool = LiveResearchCompany(
        [_ArchiveSources(tuple(sources))],
        run_id=run_id,
        schema_version=run_input.schema_version,
        as_of=run_input.as_of,
        clock=clock,
    )
    result = tool(candidate, budget)
    result.retrieval_records[-1].arguments_without_secrets["archive_conversion"] = dict(
        origin="new_archive_conversion",
        index_sha256=expected_index_sha256,
        collection_index=index,
        source_manifests=manifests,
        candidate_packet=packet,
        original_receipts=rows,
        search_crosscheck=search,
        current_composition=dict(
            scope="local_archive_transformation_only",
            external_requests_used=0,
            external_cost_usd=0,
        ),
        physical_http_requests="unmeasured",
        historical_paid_ledger="not_supplied_unverified",
        historical_tool_runtime="not_supplied_unverified",
        post_collection_selection_context=True,
    )
    return ToolResult[CompanyResearchBundle].model_validate_json(
        result.model_dump_json(), context={"execution_mode": "live"}
    )
