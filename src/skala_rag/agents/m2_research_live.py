"""Opt-in source collection and persisted Company Research State for #62.

The CLI performs public official-site research without paid LLM requests.
The run_research_to_trace API accepts a research tool configured with an approved
runtime-backed eligibility extractor and keeps admission/Technology execution.
"""

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from skala_rag.agents.m2_research import run_research_to_trace
from skala_rag.contracts import Candidate, RunInput, ToolBudget
from skala_rag.tools.company_research import LiveResearchCompany
from skala_rag.tools.official_homepage import OfficialHomepage
from skala_rag.tools.opendart import HOST, OpenDartCompany
from skala_rag.tools.source_fetch import FetchPolicy, SafeFetcher


class Clock:
    def now(self):
        return datetime.now(UTC)


def run(*, root: Path, input_path: Path, output_dir: Path, live: bool):
    """Explicit source-only smoke. Missing fact extraction remains unknown."""
    if not live:
        raise ValueError("explicit --live is required")
    config = json.loads(input_path.read_text())
    if set(config) != {"candidate", "run_input", "run_id"}:
        raise ValueError("expected candidate/run_input/run_id only")
    run_input = RunInput.model_validate(config["run_input"])
    if run_input.execution_mode != "live":
        raise ValueError("source-only smoke requires live RunInput")
    candidate = Candidate.model_validate(
        config["candidate"], context={"execution_mode": "live"}
    )
    clock = Clock()
    schema = run_input.schema_version
    key = os.environ.get("OPENDART_API_KEY")

    def fetcher(hosts):
        return SafeFetcher(
            FetchPolicy(
                allowed_schemes=frozenset({"https"}),
                allowed_hosts=hosts,
                max_bytes=5_000_000,
                timeout_seconds=30,
                max_redirects=3,
            ),
            clock=clock,
        )

    tool = LiveResearchCompany(
        [
            OfficialHomepage(
                fetcher(None), schema_version=schema, clock=clock, extractor=None
            ),
            OpenDartCompany(
                fetcher(frozenset({HOST})),
                api_key=key,
                schema_version=schema,
                clock=clock,
                max_name_matches=1,
                max_index_bytes=100_000_000,
            ),
        ],
        run_id=config["run_id"],
        schema_version=schema,
        as_of=run_input.as_of,
        clock=clock,
    )
    secrets = [
        v
        for k, v in os.environ.items()
        if any(part in k.upper() for part in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
        and v
    ]
    outcome = run_research_to_trace(
        candidate=candidate,
        run_input=run_input,
        run_id=config["run_id"],
        research_tool=tool,
        budget=ToolBudget(
            schema_version=schema,
            max_calls=3,
            max_retries=0,
            timeout_seconds=30,
            deadline=clock.now() + timedelta(minutes=10),
        ),
        root=root,
        output_dir=output_dir,
        secret_values=secrets,
        technology=None,
        runtime_observations={
            "llm_requests": 0,
            "api_cost_usd": None,
            "fact_extraction": {
                "required_for_admission": True,
                "configured": False,
                "credential_present": bool(os.environ.get("OPENAI_API_KEY")),
                "reason": "source-only CLI does not perform paid LLM extraction",
            },
            "opendart": {
                "required": False,
                "credential_present": bool(key),
                "country_supported": candidate.country == "KR",
            },
        },
    )
    # Do not print State, raw excerpts, arguments or configured credentials.
    print(json.dumps(outcome.receipt, ensure_ascii=False, indent=2))
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    run(
        root=args.root,
        input_path=args.input,
        output_dir=args.output_dir,
        live=args.live,
    )


if __name__ == "__main__":
    main()
