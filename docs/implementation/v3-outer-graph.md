# #188 fixture-only v3 outer LangGraph

This slice adds a real outer `StateGraph`; it does not wrap the complete Python
controller in a single node. `run_candidates_v3` remains the compatibility oracle.
The bounded research interfaces and rules documented in
[the #184 controller](issue184-v3-research-loop.md) are preserved. Existing documents
that call #184 a Python loop describe that original callable, not the new graph.

## Python API

Import from `skala_rag.graph.candidate_workflow_v3`:

- `build_candidate_workflow_v3(stages, evaluators, **options) -> StateGraph` builds
  the graph; callers compile it and supply a recursion limit for their finite input.
- `candidate_recursion_limit_v3(candidate_count, policy) -> int` derives an execution
  guard from a supplied population and the approved research allowance. It is not
  a candidate cap, sampling rule, or discovery policy.
- `run_candidate_workflow_v3(stages, evaluators, graph_events=None, **options)
  -> CandidateRunV3` is the direct Python callable. It streams discovery and
  normalization to a static interrupt **without a checkpointer**, derives the bound
  from the actual normalized population, then hands a detached state update to a
  second non-persistent compilation of the same node/conditional-edge flow. That
  compilation starts at `candidate_iterator` (or `selector` after discovery or
  normalization failure). Discovery and normalization are not replayed. Optional
  `graph_events` receives actual `(namespace, updates)` stream tuples, including
  the initial interrupt. An interrupt is not END; this handoff is not persistent
  resume.

Required options match the existing controller's injected fixture interfaces:
`policy`, `catalog`, `catalog_policy_version`, `run_id`, `schema_version`,
`support_check`, `applicability_assessments`, `applicability_check`,
`applicability_verifier`, `industry_evidence_dimensions`, and `clock`.
Optional `trace_events` records controller operation observations with
`controller="langgraph"`. Those observations are distinct from raw graph updates;
for example `research_ready` is an operation observation, not a graph node.
No provider, corpus, policy, or semantic default is selected here.

### Opaque callback contexts and checkpoint boundary

`CandidateStagesV3.research` still returns `object`; eligibility/collection and
`ResearchRequestV3.research` receive detached copies of that object. Custom Python
classes, nested opaque values, DTOs, and callback fields retain their types and
values: there is no conversion to string/dictionary, field dropping, pickling,
serializer allowlist expansion, or LangGraph monkeypatch. As with the oracle,
contexts must support `deepcopy`; clients, connections, and secrets are not intended
research context data. Freeze keeps its existing candidate/eligibility/Coverage
signature, not a new research-context argument.

Both public-callable compilations explicitly use `checkpointer=False`. The first
static interrupt only yields the initial validated population (or redacted
initial failure state); the rest of the real loop executes in process with no
checkpoint writes or reads. There is no cross-invocation context repository or
thread-id-keyed state. The public callable does not promise durable resume,
checkpoint DTO roundtrips, or checkpoint-safe arbitrary research objects. A caller
compiling the builder with their own saver must address that separate compatibility
boundary; this change does not relax any serializer security checks.

A fixture-only direct Python example, using the existing test harness to provide
all callbacks and DTOs:

```python
from tests.integration import test_v3_candidates as fixtures
from skala_rag.graph.candidate_workflow_v3 import run_candidate_workflow_v3

events = []
original = fixtures.run_candidates_v3


def execute(stages, evaluators, **options):
    return run_candidate_workflow_v3(stages, evaluators, graph_events=events, **options)


fixtures.run_candidates_v3 = execute
try:
    result, calls = fixtures.scenario()
finally:
    fixtures.run_candidates_v3 = original

assert result.candidate_index == 2
assert len(calls) == 10
assert any(not namespace and "selector" in updates for namespace, updates in events)
```

This harness substitution is for local fixture reproduction, not a production
registry change. Run from the repository's frozen environment with
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python <example.py>`.

## Real topology and invariants

Outer nodes are `discover`, `normalize`, `candidate_iterator`, `research`,
`eligibility`, `collect`, `coverage`, `research_gate`, `additional_research`,
`freeze`, `evaluation_join`, `score`, `decision`, `archive`, `advance`, and
`selector`. Conditional edges implement the research loop and failure/eligibility
routes. `selector` has the compiled edge to `__end__`.

- Candidates run sequentially; the first recommendation does not terminate the
  population. The deterministic selector runs once after terminal candidate outcomes.
- Initial collection consumes zero extra requests. The gate charges before the
  callback; attempts 1 and 2 have remaining allowances 1 and 0. Empty/no-op and
  recoverable responses consume allowance; no third request is issued.
- Recoverable exceptions retain current Coverage and return to the gate; successful
  responses return to Coverage. Final recoverable failure, terminal exceptions,
  invalid response, absent callback, and zero applicable denominator are technical
  failure routes, without score or investment label.
- Unknown eligibility, ineligible candidates, empty populations, no selected
  recommendation, and technical failures retain the existing distinct outcomes.
- Freeze uses the same extracted generation/admission validators as the Python
  oracle: run/candidate/schema/policy/revision/round identity, newly admitted Evidence
  preservation, and Source/related Evidence/RetrievalRecord/Chunk closure remain
  enforced. Callback inputs and frozen branch inputs are detached copies.
- `evaluation_join` invokes the existing real five-way evaluation graph. Founder,
  Market, Technology, Moat, and Business & Deal execute in parallel; the last branch
  atomically returns traction and deal_terms. All six dimensions must succeed before
  scoring. The child's failure archive/index is checked but not merged into the outer
  ledger; the outer archive and advance occur once per candidate.

## Observed P1 regression fix verification

The opaque-context regression was reproduced test-first at the **public callable**:
three representations (custom object, nested opaque dictionary, DTO containing an
opaque value) each passed through the Python oracle and then failed in
`InMemorySaver.put_writes` with `TypeError: Type is not msgpack serializable:
OpaqueResearchContext`. After replacing the checkpointed resume with the
non-persistent graph handoff, those three cases passed. Callback type/field checks,
detached mutations in eligibility/collection/additional research, callback-derived
eligibility fields at freeze, and the two-request ledger are asserted.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_v3_outer_graph.py --tb=short
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_v3_candidates.py tests/integration/test_v3_research_loop.py tests/integration/test_v3_outer_graph.py --tb=short
```

Observed results: **62 outer tests passed**, **107 scoped tests passed**. All 58
previous outer cases remain; their acceptance-case runner now uses the public
callable instead of bypassing its handoff. Added coverage includes repeated and
simultaneous opaque-context runs with the same `run_id`, detached callback fields,
and real per-invocation five-way barriers. The normalized-population test asserts
that twelve discovered candidates reduced to ten yield a bound based on **ten**,
with discovery/normalization each called once and one real initial interrupt.
Scoped Ruff and format checks passed for all three Python feature files.

The direct public-callable trace is reproducible locally:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python /Users/luk/.hermes/cache/scratch/issue188-p1fix-api-trace.py
```

`issue188-p1fix-api-trace.json` and `.log` in that scratch directory record the
public result, actual stream counts, and both compiled topologies. Observed:
entrypoints `discover` then `candidate_iterator`; normalized count 2 / recursion
limit 52; six Coverage nodes/gates, four additional requests, two freezes, two
inner `join_v3` nodes, each of the five inner branches twice, two outer
archives/advances, and one selector. Requests debit as `[1, 2, 1, 2]` with remaining
allowances `[1, 0, 1, 0]`. Terminal index is 2 and selector state route is
`__end__`; the compiled selector-to-END edge is present. No fabricated END update
or terminal checkpoint read is used. The public trace matches the oracle and emits
**no MsgPack checkpoint-deserialization warnings** because neither phase uses a saver.

An offline AST-only `graphify update .` (Gemini/Google keys unset, external LLM
flag disabled) rebuilt 4,738 nodes / 15,007 edges / 195 communities. Curated community
labels were partially replaced by hub names when the community set changed;
semantic documentation extraction and LLM relabeling were not run.

## Historical recovery verification — superseded public checkpoint path

The following receipts describe the **pre-fix** checkpointed implementation, not
current public-callable behavior. On the preserved `c41ce496`-based worktree, recovery ran:

```bash
.venv/bin/ruff check src/skala_rag/graph/candidates_v3.py src/skala_rag/graph/candidate_workflow_v3.py tests/integration/test_v3_outer_graph.py
.venv/bin/ruff format --check src/skala_rag/graph/candidates_v3.py src/skala_rag/graph/candidate_workflow_v3.py tests/integration/test_v3_outer_graph.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_v3_outer_graph.py --tb=short
```

Results: Ruff passed, all three files already formatted, **58 tests passed**. Tests
re-execute unchanged candidate/research-loop acceptance cases at the outer surface,
compare full oracle results, cover the normalized-population callable, and use a
five-party barrier plus repeated/concurrent graph invocations. The parent's earlier
103-test candidate/loop/outer result is separate evidence, not a new recovery run.

The direct recovery trace is reproducible from the recovery workspace:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python /Users/luk/.hermes/cache/scratch/issue188-recovery-trace.py
```

Its JSON receipt is
`/Users/luk/.hermes/cache/scratch/issue188-recovery-fixture-trace.json`; stdout/stderr
is preserved in `issue188-recovery-trace.log` in that scratch directory. These are
local handoff artifacts, not committed runtime persistence. The script streams the
actual compiled graph with `subgraphs=True`, records raw events and topology, and
reads terminal checkpoint state. It asserts oracle equality on the selector's
stream result and the same evaluator-call set. Observed counts: six Coverage and
six gates, four additional requests, two freezes, two inner `join_v3` nodes, each
of the five inner branches twice, two outer archives/advances, and one selector.
The two candidates terminate at index 2 with recursion limit 52, checkpoint
`next=()`, and route `__end__`. END is inferred from the actual terminal checkpoint
and compiled selector edge; no synthetic END update is inserted into the stream.

The trace emits six unregistered-DTO MsgPack checkpoint deserialization warnings.
The successful run exits zero; the warnings are not execution failure. Checkpoint
roundtrip also converts the result's `errors` tuple to a list. A first recovery
probe compared that roundtripped object to the oracle and failed; its log is retained
as `issue188-recovery-trace-initial-failure.log`. The public callable returns the
selector stream DTO, so the corrected trace compares that actual API result and
uses checkpoint reads only for terminal observations. Persistent resume/DTO
roundtrip compatibility is not established. No strict-mode bypass, serializer
allowlist expansion, or checkpoint security policy is introduced.

## Evidence boundaries and handoff

Receipt metadata separates `execution_terminated=true`,
`fixture_oracle_equal=true`, `acceptance_status="not_evaluated"`, and
`publication_allowed=false`. Fixture execution and oracle equivalence are not
semantic/report acceptance or publication permission, nor actual Web/API/RAG,
paid-provider, live-policy, final-PDF, or #96/M3 completion evidence.

Historical RED/GREEN/recovery receipts remain intact; the P1 fix changes only
`candidate_workflow_v3.py`, its outer integration tests, and this document. The
prior shared validator extraction in `candidates_v3.py` is preserved unchanged.
Independent review, full-suite, build, integration, commit/push, and GitHub actions
remain parent-owned. The new AST-only code graph receipt is separate from
historical graphify receipts. Scoring/contracts/configs and the staged #187 owner's
files were not changed. All original live gates and unresolved Company Research
retry policy remain in force.
