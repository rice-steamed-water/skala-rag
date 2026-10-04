# Lossless offline baseline → v3 evaluator adapter (#191)

This is the offline child of #96, not actual-runtime completion. Direct imports:

- `skala_rag.agents.evaluation_v3_adapter.adapt_baseline_branch_result(result, *, branch_id, snapshot, criteria, industry_evidence_dimensions)` returns `contracts.v3.EvaluationBranchResult`.
- `bind_baseline_evaluator_v3(branch_id, evaluate, *, criteria, industry_evidence_dimensions)` returns a synchronous `callback(EvaluationSnapshot)` suitable for the existing v3 evaluation graph.

Only `founder`, `market`, and `technology` are accepted. `result` must be a
baseline `contracts.evaluation.EvaluationResult`, not a Technology container,
v3 result, partial domain result, or report. Pass existing Moat and atomic
Business & Deal v3 callbacks unchanged; never bridge them through this adapter.

## Contract and generation authority

The caller supplies the original frozen snapshot and complete 23-criterion,
six-dimension catalog. The adapter detaches and revalidates mutable DTO fields,
including forged nested `model_copy` values and undeclared fields that normal
serialization could otherwise omit. It also revalidates the supplied catalog.
It does not load policy, create approval, infer person/market identity, or resolve
semantic applicability.

All original run/candidate/round/snapshot/revision/policy identity is retained
and must match the snapshot. Envelope, evaluation, assessment, gap and error
schema versions must match the snapshot's schema. Nested snapshot DTOs are
structurally revalidated; their own source/provenance schemas are not rewritten.
`validate_branch_v3` then enforces dimension criterion completeness, uniqueness,
frozen Evidence membership, company/criterion attribution, explicitly supplied
industry scope, and gap attribution. No arbitrary `True` verifier is installed.

The draft and operational catalogs share IDs/weights but their generations are
not interchangeable: `main-draft-0.1.0` output cannot enter a
`v3-operational-1.0.0` snapshot. The adapter never stamps a policy or schema to
make it fit. It compares the supplied generation, not a newly invented approval
registry. Authenticity/approval of the supplied snapshot/catalog remains the
caller's existing controller responsibility. Synthetic tests explicitly choose
matching generations; that does not prove existing evaluators can emit the
operational generation.

On success, original rubric version, ratings, rationale, evidence IDs/order,
missing reasons, applicability notes, research gaps and caveats survive exactly.
The v3-only applicability fields remain null. A Core applicability note is not
an approved N/A rule and never becomes `not_applicable`.

On technical failure, evaluations remain null and every `WorkflowError` field
is retained: ID, code, node, run/candidate, retryable, attempt, timestamp, schema
and redacted message. No Missing rating, zero score, or synthetic replacement
error is manufactured. Invalid inputs raise only
`ValueError("Invalid baseline-to-v3 adapter input")`, without embedding source
payloads or displaying their exception chain; adapter-owned detachment and
validation failures (including malformed copy hooks and recursive containers)
use that same rejection. Process-control `BaseException`s, including cancellation,
`KeyboardInterrupt` and `SystemExit`, are not caught. The existing graph owns
conversion of that rejection into terminal failure. A value not representable
under the target contract is rejected, not silently dropped.

## Binding and Technology receipts

The binder captures detached catalog/scope options and revalidates each incoming
snapshot before calling upstream. Upstream is invoked once with a detached
snapshot. Conversion is checked against an independent, unmodified frozen copy,
so upstream mutation cannot forge new allowed Evidence or a new generation.
Existing upstream retries/repairs remain the upstream evaluator's responsibility;
the binder adds none. Final snapshot copying also completes inside the
adapter-input boundary, before invocation. The real `evaluate(...)` call stays
outside that boundary: upstream exceptions propagate unchanged (including their
identity and cause) to the existing graph's redacted failure handling, without
a second call.

Technology callers explicitly return `receipt.result` and retain the original
`TechnologyEvaluation` in their own receipt store. Its `prompt_version`,
`allowed_evidence_ids`, and typed criterion → Evidence → retrieval/chunk trace
are not v3 Evaluation fields. The adapter rejects the container itself rather
than silently extracting it, discarding its trace, or stringifying it into State.

## Executable offline Python example

Run this code from the repository root after `uv sync --frozen --offline` using
the issue-local Python environment. All data below is synthetic; the Market
callback is injected terminal-envelope data, **not** PR134's absent evaluator.
The Technology evaluator uses `FakeLLM`, not a provider. There is no model,
corpus, network or paid-runtime request.

```python
from datetime import UTC, datetime
from pathlib import Path

import yaml
from tests.fixtures.loader import load_common_fixtures

from skala_rag.agents.evaluation import output_from_evaluation
from skala_rag.agents.evaluation_v3_adapter import (
    adapt_baseline_branch_result,
    bind_baseline_evaluator_v3,
)
from skala_rag.agents.technology import evaluate_technology
from skala_rag.contracts.evaluation import EvaluationResult
from skala_rag.fakes import FakeClock, FakeLLM
from skala_rag.scoring.catalog import load_policy

policy = load_policy("configs/scoring.draft.json", execution_mode="fixture")
fixtures = load_common_fixtures(policy)
snapshot = next(iter(fixtures.snapshots.values()))


def synthetic_terminal(dimension):
    evaluation = fixtures.evaluations[
        f"{snapshot.candidate_id}:{snapshot.evaluation_round}:{dimension}"
    ]
    identity = {
        name: getattr(evaluation, name)
        for name in (
            "schema_version",
            "run_id",
            "candidate_id",
            "dimension",
            "evaluation_round",
            "snapshot_id",
            "evidence_revision",
            "policy_version",
        )
    }
    return EvaluationResult(
        **identity, status="success", evaluation=evaluation, errors=[]
    )


original = synthetic_terminal("market")
market = adapt_baseline_branch_result(
    original,
    branch_id="market",
    snapshot=snapshot,
    criteria=policy.criteria,
    industry_evidence_dimensions=set(),
)
assert market.policy_version == original.policy_version == snapshot.policy_version
assert market.evaluations["market"].rubric_version == original.evaluation.rubric_version

rubric = yaml.safe_load(Path("configs/rubrics/core.yaml").read_text())
llm = FakeLLM([output_from_evaluation(synthetic_terminal("technology").evaluation)])
technology_receipts = []


def technology_terminal(detached_snapshot):
    receipt = evaluate_technology(
        detached_snapshot,
        rubric=rubric,
        llm=llm,
        policy=policy,
        clock=FakeClock(datetime(2026, 9, 1, tzinfo=UTC)),
        schema_version=snapshot.schema_version,
        execution_mode="fixture",
    )
    technology_receipts.append(receipt)
    return receipt.result


technology_callback = bind_baseline_evaluator_v3(
    "technology",
    technology_terminal,
    criteria=policy.criteria,
    industry_evidence_dimensions=set(),
)
technology = technology_callback(snapshot)
assert technology.status == "success"
assert len(llm.calls) == len(technology_receipts) == 1
assert technology_receipts[0].trace
print({"market": market.status, "technology": technology.status, "fake_calls": 1})
```

For the five-way graph, bind Founder/Market/Technology with this API and retain
existing Moat/Business & Deal callbacks in the same five-entry mapping passed to
`build_evaluation_graph_v3`. Preserve its frozen storage/controller preflight
and atomic join; no graph, admission flag, scorer or selector changes are needed.
See [the existing graph API](v3-evaluation-graph.md) and
[outer workflow boundaries](v3-outer-graph.md). The outer operational policy
still requires matching original outputs; this example's draft results are not
compatible by relabelling.

## Verification and remaining gates

- Unit tests cover exact observed/missing/failure roundtrips; detached mutation;
  dimension/catalog/generation/schema corruption; forged nested payloads;
  outside, foreign, misattributed and unauthorized industry Evidence;
  one-call binding and original-snapshot validation after upstream mutation.
- Integration tests stream the installed LangGraph using a five-party barrier:
  three adapted synthetic baseline results plus two existing-v3-shaped synthetic
  Moat/Business & Deal envelopes. Success promotes all six dimensions at one
  join. Any baseline failure, exception, wrong generation or BD half-output
  promotes zero dimensions, archives/advances once, and retains original error
  references. Existing live-mode preflight still rejects before callbacks.
- Existing Founder and Technology fixture callables are exercised with FakeLLM;
  no extra LLM call is introduced, and the original Technology trace is retained.
- Market implementation is absent on this base. Injected terminal data is tested
  without a skip; running the actual Market callable remains PR134-owner work.

This module does not complete approval artifact propagation, approved semantic
review receipts, provenance-closed actual freeze, D05/D06/D08 controller inputs,
shared runtime budget reserve/settle admission, actual five-branch execution,
scoring/report integration, or publication. Existing actual/live denials remain.
No new provider/model/corpus/policy or paid fallback is authorized.
