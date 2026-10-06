"""Source-only composition for the existing outer graph, never live scoring.

Inputs are capture replay, not a new topic Discovery measurement. The only newly
invoked provider is the existing OfficialHomepage with no extractor. Neither
references, successful DTO validation nor the historical policy pin confer source
approval, semantic authority, campaign approval or permission for network calls.
"""

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from skala_rag import run_settings
from skala_rag.agents.discovery import accept_discovery
from skala_rag.agents.m2_research import assemble_research_state
from skala_rag.contracts import (
    Candidate,
    CompanyResearchBundle,
    DiscoveryBundle,
    RunInput,
    ToolBudget,
    ToolResult,
)
from skala_rag.contracts.interfaces import Clock
from skala_rag.scoring.approval_registry import pinned_approval_registry
from skala_rag.scoring.catalog import load_policy
from skala_rag.scoring.selector_v3 import _select_best_v3
from skala_rag.scoring.v3_policy import V3Policy, load_v3_policy
from skala_rag.tools.company_research import LiveResearchCompany, same_site, url_host
from skala_rag.tools.official_homepage import OfficialHomepage
from skala_rag.tools.provider_scope import EXCLUDED_PROVIDERS
from skala_rag.tools.source_fetch import SafeFetcher, check_as_of

ROOT = Path(__file__).resolve().parents[2]


def _json(value):
    def encode(item):
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        raise TypeError("source-only artifacts require JSON/DTO data")

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=encode
    )


def _schema_closure(value, schema):
    if isinstance(value, dict):
        if "schema_version" in value and value["schema_version"] != schema:
            raise ValueError("source-only schema generation mismatch")
        for item in value.values():
            _schema_closure(item, schema)
    elif isinstance(value, list):
        for item in value:
            _schema_closure(item, schema)


def _declared_generation(value, expected):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in expected and item != expected[key]:
                raise ValueError("source-only declared generation mismatch")
            _declared_generation(item, expected)
    elif isinstance(value, list):
        for item in value:
            _declared_generation(item, expected)


@dataclass(frozen=True)
class SourceOnlyV3:
    """Pinned data plus an out-of-State fetcher factory; not an approval token."""

    run_id: str
    run_input_json: str
    discovery_json: str
    budget_json: str
    run_profile: run_settings.RunProfile
    provider: str | None
    replay_scope: str
    fetcher_factory: Callable[[], SafeFetcher] | None
    clock: Clock
    research_replays_json: str = "{}"
    input_binding_sha256: str = ""
    fixed_candidate_json: str | None = None

    def binding_digest(self):
        # Integrity only: a hash is not a signature, approval or source authority.
        return hashlib.sha256(
            _json(
                dict(
                    run_id=self.run_id,
                    run_input=json.loads(self.run_input_json),
                    discovery=json.loads(self.discovery_json),
                    budget=json.loads(self.budget_json),
                    profile=asdict(self.run_profile),
                    provider=self.provider,
                    replay_scope=self.replay_scope,
                    research_replays=json.loads(self.research_replays_json),
                    **(
                        dict(
                            fixed_candidate_input=json.loads(self.fixed_candidate_json)
                        )
                        if self.fixed_candidate_json is not None
                        else {}
                    ),
                )
            ).encode()
        ).hexdigest()

    @property
    def run_input(self):
        return RunInput.model_validate_json(self.run_input_json)

    @property
    def discovery_result(self):
        if self.fixed_candidate_json is not None:
            raise ValueError("fixed candidate input is not a Discovery ToolResult")
        return ToolResult[DiscoveryBundle].model_validate_json(
            self.discovery_json, context={"execution_mode": "live"}
        )

    @property
    def candidate_bundle(self):
        if self.fixed_candidate_json is not None:
            return DiscoveryBundle.model_validate_json(
                self.fixed_candidate_json, context={"execution_mode": "live"}
            )
        return self.discovery_result.data

    @property
    def budget(self):
        return ToolBudget.model_validate_json(self.budget_json)

    def validate(self, *, policy, run_id, schema_version, run_profile):
        if self.binding_digest() != self.input_binding_sha256:
            raise ValueError("source-only pinned input binding mismatch")
        run = self.run_input
        run_settings._validate_profile(self.run_profile)
        fixed = self.fixed_candidate_json is not None
        if fixed:
            if (
                self.replay_scope != "fixed_candidate_input"
                or self.provider is not None
                or self.fetcher_factory is not None
                or self.discovery_json != "null"
            ):
                raise ValueError(
                    "offline source-only requires fixed input and no provider"
                )
        elif (
            self.provider in EXCLUDED_PROVIDERS or self.provider != "official-homepage"
        ):
            raise ValueError("source-only provider excluded or not supported")
        if not fixed and self.replay_scope != "discovery_bundle_replay":
            raise ValueError(
                "new Discovery producer is not configured; bundle replay required"
            )
        if (
            run.execution_mode != "live"
            or self.run_id != run_id
            or run.schema_version != schema_version
            or run.policy_version != policy.policy_version
            or self.run_profile != run_profile
            or self.run_profile.run_id != run_id
        ):
            raise ValueError("source-only run/policy/profile generation mismatch")
        budget = self.budget
        if (
            budget.schema_version != schema_version
            or budget.max_calls < 1
            or budget.max_retries != 0
        ):
            raise ValueError(
                "source-only finite nonzero budget and zero retries required"
            )
        registry = pinned_approval_registry(ROOT)
        if not registry.verify_policy(registry.policy_approvals().operational, policy):
            raise ValueError("source-only pinned operational content mismatch")
        bundle = self.candidate_bundle if fixed else self.accept_discovery().bundle
        if fixed:
            self.validate_candidate_bundle(bundle)
        candidates = {c.candidate_id: c for c in bundle.candidates} if bundle else {}
        captures = json.loads(self.research_replays_json)
        if not isinstance(captures, dict) or not captures.keys() <= candidates.keys():
            raise ValueError("source-only replay candidate closure mismatch")
        for cid, payload in captures.items():
            self.validate_research_result(
                ToolResult[CompanyResearchBundle].model_validate(
                    payload, context={"execution_mode": "live"}
                ),
                cid,
            )

    def validate_research_result(self, result, cid, *, candidate=None):
        run = self.run_input
        if candidate is None:
            candidate = next(
                c for c in self.candidate_bundle.candidates if c.candidate_id == cid
            )
        names = {candidate.canonical_name, *candidate.aliases}
        _schema_closure(result.model_dump(mode="json"), run.schema_version)
        expected = dict(
            run_id=self.run_id,
            candidate_id=cid,
            schema_version=run.schema_version,
            policy_version=run.policy_version,
            corpus_version=run.corpus_version,
            as_of=run.as_of.isoformat(),
        )
        for record in result.retrieval_records:
            if (
                record.run_id != self.run_id
                or record.candidate_id != cid
                or record.started_at > record.finished_at
            ):
                raise ValueError("source-only research history attribution mismatch")
            arguments = record.arguments_without_secrets
            _declared_generation(arguments, expected)
            # Only declared candidate fields / known producer queries attribute
            # identity. Opaque queries and unknown metadata remain unverified.
            if (
                "canonical_name" in arguments
                and arguments["canonical_name"] not in names
            ):
                raise ValueError("source-only research name attribution mismatch")
            if "homepage_url" in arguments and (
                url_host(arguments["homepage_url"]) != url_host(candidate.homepage_url)
            ):
                raise ValueError("source-only research homepage attribution mismatch")
            if record.tool_name == "company-research" and record.query is not None:
                if record.query not in names:
                    raise ValueError(
                        "source-only research summary query attribution mismatch"
                    )
            if record.tool_name == "company-research/official-homepage":
                declared_url = arguments.get("url")
                if declared_url is not None and not same_site(
                    declared_url, candidate.homepage_url
                ):
                    raise ValueError("source-only research URL attribution mismatch")
                if record.query is not None and record.query.startswith(
                    ("https://", "http://")
                ):
                    if not same_site(record.query, candidate.homepage_url):
                        raise ValueError(
                            "source-only research URL query attribution mismatch"
                        )
        for error in result.errors:
            if error.run_id != self.run_id or error.candidate_id not in (None, cid):
                raise ValueError("source-only research error attribution mismatch")
        if result.data is not None and (
            result.data.profile.candidate_id != cid
            or result.data.profile.as_of != run.as_of
        ):
            raise ValueError("source-only research profile attribution mismatch")

    def accept_discovery(self):
        result = self.discovery_result
        run = self.run_input
        _schema_closure(result.model_dump(mode="json"), run.schema_version)
        outcome = accept_discovery(result)
        ids = (
            {c.candidate_id for c in outcome.bundle.candidates}
            if outcome.bundle
            else set()
        )
        for record in outcome.retrieval_records:
            if (
                record.run_id != self.run_id
                or record.candidate_id not in (None, *ids)
                or record.started_at > record.finished_at
            ):
                raise ValueError("source-only discovery history attribution mismatch")
            _declared_generation(
                record.arguments_without_secrets,
                dict(
                    run_id=self.run_id,
                    schema_version=run.schema_version,
                    policy_version=run.policy_version,
                    corpus_version=run.corpus_version,
                    as_of=run.as_of.isoformat(),
                ),
            )
        for error in outcome.errors:
            if error.run_id != self.run_id or error.candidate_id not in (None, *ids):
                raise ValueError("source-only discovery error attribution mismatch")
        if outcome.bundle:
            self.validate_candidate_bundle(outcome.bundle)
        return outcome

    def validate_candidate_bundle(self, bundle):
        run = self.run_input
        _schema_closure(bundle.model_dump(mode="json"), run.schema_version)
        if any(c.country not in run.countries for c in bundle.candidates):
            raise ValueError("source-only discovery country mismatch")
        if any(not check_as_of(s, run.as_of).admitted for s in bundle.sources.values()):
            raise ValueError("source-only discovery cutoff mismatch")

    def validate_fresh_admission(self):
        """Timing admission for new no-publication-date fetches, never replay facts."""
        now = self.clock.now()
        if now.date() > self.run_input.as_of:
            raise ValueError("source-only fresh fetch clock exceeds as_of")
        if self.budget.deadline is not None and now >= self.budget.deadline:
            raise ValueError("source-only fresh fetch deadline expired")

    def research(self, candidate, tool, *, on_provider_call=None):
        run = self.run_input
        candidate = Candidate.model_validate(
            candidate, context={"execution_mode": "live"}
        )
        captures = json.loads(self.research_replays_json)
        captured = captures.get(candidate.candidate_id)
        if captured is not None:
            result = ToolResult[CompanyResearchBundle].model_validate(
                captured, context={"execution_mode": "live"}
            )
        else:
            if self.fixed_candidate_json is not None:
                raise ValueError(
                    "offline source-only selected research capture missing"
                )
            self.validate_fresh_admission()
            if on_provider_call is not None:
                on_provider_call()
            result = tool(candidate.model_copy(deep=True), self.budget)
            result = ToolResult[CompanyResearchBundle].model_validate(
                result.model_dump(mode="python", warnings="error"),
                context={"execution_mode": "live"},
            )
        self.validate_research_result(
            result, candidate.candidate_id, candidate=candidate
        )
        assembled = assemble_research_state(
            candidate=candidate, run_input=run, run_id=self.run_id, result=result
        )
        return dict(
            result=result.model_dump(mode="json"),
            state=deepcopy(assembled.state),
            receipt=deepcopy(assembled.receipt),
            input_scope="captured_result_replay"
            if captured is not None
            else "provider_call",
        )

    def has_replay(self, cid):
        return cid in json.loads(self.research_replays_json)

    def make_tool(self, *, on_configuration_attempt=None):
        # Called only after policy/run/budget/exclusions and selection are pinned.
        if self.fixed_candidate_json is not None:
            raise ValueError("offline source-only provider configuration forbidden")
        self.validate_fresh_admission()
        if on_configuration_attempt is not None:
            on_configuration_attempt()
        fetcher = self.fetcher_factory()
        if type(fetcher) is not SafeFetcher:
            raise ValueError("source-only requires existing SafeFetcher")
        return LiveResearchCompany(
            [
                OfficialHomepage(
                    fetcher,
                    schema_version=self.run_input.schema_version,
                    clock=self.clock,
                    extractor=None,
                )
            ],
            run_id=self.run_id,
            schema_version=self.run_input.schema_version,
            as_of=self.run_input.as_of,
            clock=self.clock,
        )


def prepare_source_only_v3(
    *,
    run_id: str,
    run_input: RunInput,
    discovery_result: ToolResult[DiscoveryBundle],
    run_profile: run_settings.RunProfile,
    budget: ToolBudget,
    provider: str,
    replay_scope: str,
    fetcher_factory: Callable[[], SafeFetcher],
    clock: Clock,
    research_replays: Mapping[str, ToolResult[CompanyResearchBundle]] | None = None,
) -> SourceOnlyV3:
    """Reject unsafe bindings before any fetcher/provider/model configuration."""
    run = RunInput.model_validate(run_input.model_dump(mode="python", warnings="error"))
    run_settings._validate_profile(run_profile)
    result = ToolResult[DiscoveryBundle].model_validate(
        discovery_result.model_dump(mode="python", warnings="error"),
        context={"execution_mode": "live"},
    )
    budget = ToolBudget.model_validate(
        budget.model_dump(mode="python", warnings="error")
    )
    boundary = SourceOnlyV3(
        run_id,
        run.model_dump_json(),
        result.model_dump_json(),
        budget.model_dump_json(),
        deepcopy(run_profile),
        provider,
        replay_scope,
        fetcher_factory,
        clock,
        _json(
            {
                cid: ToolResult[CompanyResearchBundle]
                .model_validate(
                    value.model_dump(mode="python", warnings="error"),
                    context={"execution_mode": "live"},
                )
                .model_dump(mode="json")
                for cid, value in (research_replays or {}).items()
            }
        ),
    )
    boundary = replace(boundary, input_binding_sha256=boundary.binding_digest())
    boundary.validate(
        policy=load_v3_policy(
            ROOT / "configs/scoring.v3.json", execution_mode="fixture"
        ),
        run_id=run_id,
        schema_version=run.schema_version,
        run_profile=run_profile,
    )
    return boundary


def prepare_offline_source_only_v3(
    *,
    run_id: str,
    run_input: RunInput,
    candidate_bundle: DiscoveryBundle,
    research_captures: Mapping[str, ToolResult[CompanyResearchBundle]],
    run_profile: run_settings.RunProfile,
    budget: ToolBudget,
    clock: Clock,
) -> SourceOnlyV3:
    """Pin caller-owned fixed candidates and original captures; never collect."""
    run = RunInput.model_validate(run_input.model_dump(mode="python", warnings="error"))
    bundle = DiscoveryBundle.model_validate(
        candidate_bundle.model_dump(mode="python", warnings="error"),
        context={"execution_mode": "live"},
    )
    budget = ToolBudget.model_validate(
        budget.model_dump(mode="python", warnings="error")
    )
    boundary = SourceOnlyV3(
        run_id=run_id,
        run_input_json=run.model_dump_json(),
        discovery_json="null",
        budget_json=budget.model_dump_json(),
        run_profile=deepcopy(run_profile),
        provider=None,
        replay_scope="fixed_candidate_input",
        fetcher_factory=None,
        clock=clock,
        research_replays_json=_json(
            {
                cid: ToolResult[CompanyResearchBundle]
                .model_validate(
                    value.model_dump(mode="python", warnings="error"),
                    context={"execution_mode": "live"},
                )
                .model_dump(mode="json")
                for cid, value in research_captures.items()
            }
        ),
        fixed_candidate_json=bundle.model_dump_json(),
    )
    boundary = replace(boundary, input_binding_sha256=boundary.binding_digest())
    boundary.validate(
        policy=load_v3_policy(
            ROOT / "configs/scoring.v3.json", execution_mode="fixture"
        ),
        run_id=run_id,
        schema_version=run.schema_version,
        run_profile=run_profile,
    )
    return boundary


def select_source_only_terminal_v3(
    rows: Sequence[Mapping], policy: V3Policy, *, run_id, schema_version
):
    """Narrow no-selection gate; eligible is admitted only as scoreless failed."""
    registry = pinned_approval_registry(ROOT)
    if not registry.verify_policy(registry.policy_approvals().operational, policy):
        raise ValueError("source-only pinned operational content mismatch")
    allowed_fields = {
        "candidate_id",
        "eligibility_status",
        "status",
        "label",
        "normalized_score",
        "weighted_missing_pct",
        "applicable_weight",
        "score_summary_id",
        "decision_id",
    }
    for row in rows:
        if (
            not row.keys() <= allowed_fields
            or row["eligibility_status"] not in ("unknown", "ineligible", "eligible")
            or (row["eligibility_status"] == "eligible" and row["status"] != "failed")
            or row["status"] not in ("eligibility_unknown", "ineligible", "failed")
            or any(
                row.get(field) is not None
                for field in (
                    "label",
                    "normalized_score",
                    "weighted_missing_pct",
                    "applicable_weight",
                    "score_summary_id",
                    "decision_id",
                )
            )
        ):
            raise ValueError(
                "source-only selector accepts only scoreless noneligible "
                "or failed terminal rows"
            )
    selection = _select_best_v3(
        rows,
        numeric=policy.numeric,
        policy_version=policy.policy_version,
        run_id=run_id,
        schema_version=schema_version,
    )
    if any(row["status"] == "failed" for row in rows):
        selection = replace(
            selection,
            reason="SOURCE_ONLY_TECHNICAL_FAILURE"
            if all(row["status"] == "failed" for row in rows)
            else "SOURCE_ONLY_PARTIAL_FAILURE",
        )
    return selection


def save_source_only_v3(
    result, *, output_dir: Path, trace_events, graph_events=(), secret_values=()
):
    """Private read-back-verified JSON artifacts. Manifest has no self-hash."""
    target = Path(output_dir)
    if target.exists():
        raise ValueError("source-only output directory must be new")
    observed_nodes = [
        dict(
            namespace=list(namespace),
            node=name,
            candidate_id=update["data"].get("cid"),
            candidate_index=update["data"].get("index"),
            route=update["data"].get("route"),
        )
        for namespace, updates in graph_events
        for name, update in updates.items()
        if name != "__interrupt__"
    ]
    payloads = {
        "candidate-run.json": _json(asdict(result)),
        "trace.json": _json(trace_events),
        "graph-events.json": _json(observed_nodes),
    }
    manifest = dict(
        run_id=result.run_id,
        execution_mode=result.execution_mode,
        execution_scope="source_only",
        replay_scope=result.replay_scope,
        run_input=result.source_only_detail["run_input"],
        budget=result.source_only_detail["budget"],
        budget_scope=result.source_only_detail["budget_scope"],
        replay_budget_scope=result.source_only_detail["replay_budget_scope"],
        provider=result.source_only_detail.get("provider", "official-homepage"),
        profile=result.source_only_detail["profile"],
        input_binding_sha256=result.source_only_detail["input_binding_sha256"],
        capture_replay=result.source_only_detail["capture_replay"],
        past_paid_ledger="not_supplied_unverified",
        usage=dict(
            scope="this_source_only_composition_only",
            **result.source_only_detail["usage"],
        ),
        policy_version=result.policy_version,
        semantic_review="unreviewed",
        evaluation="not_started",
        scoring="not_started",
        publication_allowed=False,
        artifacts={
            name: hashlib.sha256(payload.encode()).hexdigest()
            for name, payload in payloads.items()
        },
    )
    payloads["manifest.json"] = _json(manifest)
    for payload in payloads.values():
        if any(
            secret and (secret in payload or json.dumps(secret)[1:-1] in payload)
            for secret in secret_values
        ):
            raise ValueError("source-only artifact contains configured secret")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(mode=0o700)
    for name, payload in payloads.items():
        fd = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
        if (target / name).read_bytes() != payload.encode():
            raise OSError("source-only artifact read-back mismatch")


def run_source_only_v3(
    boundary: SourceOnlyV3,
    *,
    output_dir: Path,
    graph_events=None,
    trace_events=None,
    secret_values=(),
):
    """Execute the existing outer nodes; no alternate controller or fake evaluator."""
    import asyncio

    from langgraph.errors import NodeCancelledError

    from skala_rag.graph import candidate_workflow_v3 as outer

    if Path(output_dir).exists():
        raise ValueError("source-only output directory must be new")
    trace = []
    events = []
    policy = load_v3_policy(ROOT / "configs/scoring.v3.json", execution_mode="fixture")
    catalog = load_policy(ROOT / "configs/scoring.draft.json", execution_mode="fixture")
    try:
        result = outer.run_candidate_workflow_v3(
            None,
            {},
            source_only=boundary,
            policy=policy,
            catalog=catalog,
            catalog_policy_version=catalog.policy_version,
            run_id=boundary.run_id,
            schema_version=boundary.run_input.schema_version,
            run_profile=boundary.run_profile,
            support_check=None,
            applicability_assessments=None,
            applicability_check=None,
            applicability_verifier=None,
            industry_evidence_dimensions=(),
            clock=boundary.clock.now,
            trace_events=trace,
            graph_events=events,
        )
    except NodeCancelledError as exc:
        # Sync LangGraph wraps cancellation; do not archive it as missing facts.
        if isinstance(exc.__cause__, asyncio.CancelledError):
            raise exc.__cause__ from None
        raise
    if trace_events is not None:
        trace_events.extend(deepcopy(trace))
    if graph_events is not None:
        graph_events.extend(deepcopy(events))
    save_source_only_v3(
        result,
        output_dir=output_dir,
        trace_events=trace,
        graph_events=events,
        secret_values=secret_values,
    )
    return result
