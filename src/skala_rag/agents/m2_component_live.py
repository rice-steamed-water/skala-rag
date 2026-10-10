"""Opt-in #62 Company Research → RAG → Evidence → Technology component runner.

Default/dry-run execution makes no network calls. No model download, full Graph,
investment recommendation, or whole-M2 completion is performed.
"""

import argparse
import hashlib
import json
import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from skala_rag.agents.eligibility_extraction import LLMEligibilityExtractor
from skala_rag.agents.m2_llm import M2LLMs
from skala_rag.agents.m2_local_rag import LocalRAG
from skala_rag.agents.m2_research import (
    ResearchState,
    run_research_to_trace,
    save_research_state,
)
from skala_rag.agents.m2_research_live import Clock
from skala_rag.agents.m2_trace import M2Trace, TraceInvalid, run_technology_trace
from skala_rag.contracts import Candidate, RunInput, ToolBudget
from skala_rag.contracts.ids import evaluation_key
from skala_rag.prompt.evidence_extraction import (
    SYSTEM_PROMPT,
    ExtractionOutput,
    build_user_prompt,
)
from skala_rag.scoring.catalog import load_policy
from skala_rag.settings import (
    RuntimeDocument,
    load_runtime_document,
    resolve_environment_credential,
)
from skala_rag.tools.company_research import LiveResearchCompany
from skala_rag.tools.official_homepage import OfficialHomepage
from skala_rag.tools.opendart import HOST, OpenDartCompany
from skala_rag.tools.source_fetch import FetchPolicy, SafeFetcher


def run(
    *,
    root: Path,
    input_path: Path,
    output_dir: Path,
    live: bool = False,
    runtime_document: RuntimeDocument | None = None,
):
    config = json.loads(input_path.read_text())
    expected = {
        "candidate",
        "run_input",
        "run_id",
        "local_rag",
        "domain_definition",
        "campaign_cost_usd_accounted",
        "rubric_path",
    }
    if set(config) != expected:
        raise ValueError("component input fields mismatch")
    candidate = Candidate.model_validate(
        config["candidate"], context={"execution_mode": "live"}
    )
    run_input = RunInput.model_validate(config["run_input"])
    run_id = config["run_id"]
    if (
        run_input.execution_mode != "live"
        or not isinstance(run_id, str)
        or not run_id.strip()
    ):
        raise ValueError("explicit live RunInput/run ID required")
    if candidate.country not in run_input.countries:
        raise ValueError("candidate country outside run scope")
    if (
        not isinstance(config["domain_definition"], str)
        or not config["domain_definition"].strip()
    ):
        raise ValueError("explicit domain definition required")
    target = output_dir.resolve()
    if (
        target == (root / "outputs").resolve()
        or not target.is_relative_to((root / "outputs").resolve())
        or target.exists()
    ):
        raise ValueError("new output directory under outputs required")
    if not isinstance(config["campaign_cost_usd_accounted"], str):
        raise ValueError("explicit decimal campaign accounting required")
    campaign = Decimal(config["campaign_cost_usd_accounted"])
    if (
        not campaign.is_finite()
        or campaign < 0
        or campaign + Decimal("1.00") > Decimal("3.00")
    ):
        raise ValueError("campaign must have room for the USD 1 run bound")
    policy = load_policy(root / "configs/scoring.draft.json", execution_mode="fixture")
    rubric = yaml.safe_load((root / config["rubric_path"]).read_text())
    if run_input.policy_version != policy.policy_version:
        raise ValueError("component policy/rubric mismatch")
    if rubric.get("rubric_version") != "core-0.1.0":
        raise ValueError("component requires the approved core rubric version")
    local = config["local_rag"]
    if set(local) != {"model_path", "store_path", "receipt_path", "query"}:
        raise ValueError("local RAG input fields mismatch")
    if not isinstance(local["query"], str) or not local["query"].strip():
        raise ValueError("explicit retrieval query required")
    document = (
        runtime_document if runtime_document is not None else load_runtime_document()
    )
    if runtime_document is not None:
        RuntimeDocument.model_validate_json(
            runtime_document.model_dump_json(), strict=True
        )
    research_profile = document.profiles.m2_research
    # Provider accounting charges once per fetch, not once per redirect hop.
    if research_profile.fetch.max_redirects != 0:
        raise ValueError("component source requests require zero redirects")
    rag = LocalRAG(
        root=root,
        runtime_document=document,
        **{
            name: root / local[name]
            for name in ("model_path", "store_path", "receipt_path")
        },
    )
    if rag.snapshot.corpus_version != run_input.corpus_version:
        raise ValueError("component corpus mismatch")
    if not any(
        candidate.candidate_id in c.candidate_ids for c in rag.snapshot.bundle.chunks
    ):
        raise ValueError("candidate has no approved local chunks")
    key = resolve_environment_credential("OPENAI_API_KEY")
    preflight = dict(
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        status="preflight_verified",
        execution_started=False,
        whole_m2_verified=False,
        credential_present=bool(key),
        corpus_version=rag.snapshot.corpus_version,
        index_version=rag.snapshot.index_version,
        embedding_model=rag.snapshot.embedding_model,
        embedding_revision=rag.snapshot.embedding_revision,
        policy_status=policy.status,
        rubric_status=rubric["status"],
        rubric_sha256=hashlib.sha256(
            (root / config["rubric_path"]).read_bytes()
        ).hexdigest(),
        policy_sha256=hashlib.sha256(
            (root / "configs/scoring.draft.json").read_bytes()
        ).hexdigest(),
        account_credit_verified=False,
        actual_cost_usd=None,
        pending_real_evidence=[
            "company eligibility",
            "LLM extraction",
            "Technology evaluation",
        ],
        missing_readiness=[
            *([] if key else ["OPENAI_API_KEY"]),
            *([] if rubric.get("status") == "approved" else ["approved core rubric"]),
        ],
    )
    if not live:
        return preflight
    if not key:
        raise ValueError("OPENAI_API_KEY required before any source/API request")
    if rubric.get("status") != "approved":
        raise ValueError("approved core rubric required before any source/API request")
    clock = Clock()
    deadline = clock.now() + timedelta(seconds=research_profile.deadline_seconds)
    llms = M2LLMs(
        api_key=key,
        run_id=run_id,
        candidate_id=candidate.candidate_id,
        schema_version=run_input.schema_version,
        execution_mode="live",
        clock=clock,
        deadline=deadline,
        runtime_document=document,
    )
    schema = run_input.schema_version
    extractor = LLMEligibilityExtractor(
        llms.stages["eligibility"],
        as_of=run_input.as_of,
        domain_definition=config["domain_definition"],
        max_input_chars=research_profile.eligibility_max_input_chars,
    )

    def fetcher(hosts):
        # Redirects are disabled so the public-provider cap bounds physical requests.
        return SafeFetcher(
            FetchPolicy(
                allowed_schemes=frozenset(research_profile.fetch.allowed_schemes),
                allowed_hosts=hosts,
                max_bytes=research_profile.fetch.max_bytes,
                timeout_seconds=research_profile.fetch.timeout_seconds,
                max_redirects=research_profile.fetch.max_redirects,
            ),
            clock=clock,
        )

    tool = LiveResearchCompany(
        [
            OfficialHomepage(
                fetcher(None), schema_version=schema, clock=clock, extractor=extractor
            ),
            OpenDartCompany(
                fetcher(frozenset({HOST})),
                api_key=resolve_environment_credential("OPENDART_API_KEY"),
                schema_version=schema,
                clock=clock,
                max_name_matches=research_profile.max_name_matches,
                max_index_bytes=research_profile.max_index_bytes,
            ),
        ],
        run_id=run_id,
        schema_version=schema,
        as_of=run_input.as_of,
        clock=clock,
    )
    secrets = [
        v
        for k, v in os.environ.items()
        if v
        and any(
            part in k.upper() for part in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
        )
    ]

    def technology(state):
        retrieved = rag.retrieve(
            candidate=candidate,
            run_input=run_input,
            run_id=run_id,
            query=local["query"],
            clock=clock,
            deadline=deadline,
        )
        selected = select_bounded_chunk(
            retrieved,
            candidate=candidate,
            run_input=run_input,
            policy=policy,
            llm=llms.stages["evidence"],
        )
        trace = run_technology_trace(
            candidate=candidate,
            state=state,
            run_input=run_input,
            retrieval=retrieved,
            selected_chunk_ids=selected,
            extract_llm=llms.stages["evidence"],
            evaluate_llm=llms.stages["technology"],
            policy=policy,
            rubric=rubric,
            clock=clock,
            run_id=run_id,
            index_version=rag.snapshot.index_version,
            schema_version=schema,
            allowed_source_ids=sorted(
                set(state["sources"]) | set(rag.snapshot.bundle.sources)
            ),
            max_repairs=1,
        )
        result = trace.evaluation.result
        key = evaluation_key(
            result.candidate_id, result.evaluation_round, result.dimension
        )
        trace.state["evaluation_results"][key] = result.model_dump(mode="json")
        trace.state["evaluations"][key] = result.evaluation.model_dump(mode="json")
        save_research_state(
            ResearchState(trace.state, trace.receipt),
            root=root,
            output_dir=output_dir / "technology",
            secret_values=secrets,
        )
        return trace

    terminal = {**preflight, "execution_started": True, "status": "component_failed"}
    try:
        outcome = run_research_to_trace(
            candidate=candidate,
            run_input=run_input,
            run_id=run_id,
            research_tool=tool,
            budget=ToolBudget(
                schema_version=schema,
                max_calls=research_profile.retrieval_max_calls,
                max_retries=research_profile.retrieval_max_retries,
                timeout_seconds=research_profile.retrieval_timeout_seconds,
                deadline=deadline,
            ),
            root=root,
            output_dir=output_dir,
            secret_values=secrets,
            technology=technology,
        )
        terminal["status"] = (
            outcome.receipt["status"]
            if isinstance(outcome, M2Trace)
            else outcome.receipt["technology_status"]
        )
        terminal["stage_receipt"] = outcome.receipt
        if isinstance(outcome, ResearchState):
            if outcome.state.get("run_outcome") == "technical_failure":
                terminal["status"] = "technical_failure"
        return terminal
    finally:
        terminal["llm_runtime"] = llms.observations()
        # Keep usage even if extraction/evaluation fails. No provider exception text.
        report = json.dumps(terminal, ensure_ascii=False, indent=2)
        if any(
            secret in report or json.dumps(secret)[1:-1] in report for secret in secrets
        ):
            raise ValueError("terminal observation contains configured secret")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.mkdir(mode=0o700)
        fd = os.open(
            target / "component-receipt.json",
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(fd, "w") as stream:
            stream.write(report)


def select_bounded_chunk(retrieved, *, candidate, run_input, policy, llm):
    """First returned chunk fitting the approved request bound, without truncation."""
    if retrieved.status != "ok" or retrieved.data is None:
        raise TraceInvalid("retrieval did not return successful chunks")
    criteria = sorted(
        c.criterion_id for c in policy.criteria if c.dimension == "technology"
    )
    for chunk in retrieved.data.chunks:
        user = build_user_prompt(
            source_text=chunk.text,
            scope=chunk.scope,
            target_names=[candidate.canonical_name, *candidate.aliases],
            as_of=run_input.as_of,
            criterion_ids=criteria,
        )
        try:
            llm.allowance_for(SYSTEM_PROMPT, user, ExtractionOutput)
        except ValueError:
            continue
        return [chunk.chunk_id]
    raise TraceInvalid("no returned chunk fits the approved input limit")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "input", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    receipt = run(
        root=args.root,
        input_path=args.input,
        output_dir=args.output_dir,
        live=args.live,
    )
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    if args.live and receipt["status"] != "technology_component_trace_verified":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
