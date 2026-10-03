# #184 bounded v3 Coverage research controller

Offline fixture integration only. `run_candidates_v3` remains a synchronous Python
controller. Its research loop is **not LangGraph**; it invokes the existing real
five-branch LangGraph evaluation graph once per successfully frozen eligible
candidate, with Business & Deal's two dimensions still atomic. All candidates
finish before the unchanged deterministic selector runs. There is no assessment
back-edge into research, provider adoption, live-policy gate opening or paid call.

## Explicit interfaces

The original six `CandidateStagesV3` callbacks remain positional/backward
compatible for sufficient coverage. Optional callbacks added by this slice:

```python
additional_research(request: ResearchRequestV3) -> ResearchResponseV3
freeze_with_evidence(candidate: dict, eligibility: EligibilityResult,
                     coverage: CoverageResult,
                     evidence: tuple[Evidence, ...]) -> EvaluationSnapshot | dict
unresolved_conflicts(candidate: dict,
                     evidence: tuple[Evidence, ...]) -> Sequence[str]
gap_templates(candidate: dict,
              coverage: CoverageResult) -> Sequence[ResearchGap | dict]
```

All callback inputs are detached from controller-owned state. Request fields are
`run_id`, `candidate`, original Company `research`, current v3 `coverage`, all
admitted `evidence`, charged `attempt` (1 or 2), `remaining_requests`, and
`research_gaps`. The Coverage includes missing criterion IDs and actual unresolved
conflicts. `gap_templates`, if supplied, must provide exactly one open criterion
gap per missing criterion, including caller-owned query/field/reason/history.
Existing `build_research_gaps_v3` validates/sorts them; no queries or rules are
invented when templates are absent (`research_gaps=()`). The callback can use the
Coverage criterion targets directly or wrap existing EvidenceResearch adapters.
This slice does not rewrite those adapters or supply a provider.

`ResearchResponseV3(candidate_id, base_evidence_revision, evidence)` is an
explicit synchronous delta envelope. Candidate and base revision must match the
request's controller generation. Evidence DTOs are validated in fixture mode,
with current schema and candidate/industry scope. Duplicate IDs within a batch,
foreign Evidence, and an existing ID with a changed full JSON payload are invalid.
An identical existing item is a no-op. New IDs are admitted and increment the
candidate's evidence revision **once per changed batch**, not per request/item.
Empty/no-op responses do not increment revision. The next Coverage always consumes
the admitted union and the current revision, not a synthetic success flag.

If `unresolved_conflicts` is absent, explicit `Evidence.conflicts_with` endpoints
are passed conservatively to Coverage. Unknown conflict endpoints fail validation;
no default conflict resolution is adopted. The callback, when supplied, owns the
actual unresolved list and receives current detached evidence on each pass.

## Accounting and technical failure

The controller consumes `policy.research.additional_requests_per_candidate=2`,
approved in #82. Initial collection is excluded; the counter increases **before**
calling `additional_research`. Counters are isolated per candidate. Empty/no-op
responses and exceptions consume a request. Ready Coverage consumes zero requests.
Deficient Coverage without the callback fails with `RESEARCH_CALLBACK_REQUIRED`;
it never calls freeze/evaluate pretending research succeeded.

Only the explicit `RecoverableResearchFailure` exception is recoverable. Each
occurrence is recorded as a uniquely identified, redacted, retryable WorkflowError
with the charged attempt. No exception message/trace/provider payload is copied.
After a recoverable failure the unchanged current Coverage remains valid, and
another bounded request may run. A subsequent successful (even empty) response
permits the ordinary exhausted-evidence path. If the final request fails
recoverably, `RESEARCH_FAILED_EXHAUSTED` terminates the candidate technically,
without score or label; failures are not reclassified as investment Missing.
All other callback exceptions, including budget/terminal exceptions, become
`RESEARCH_TERMINAL_FAILURE` immediately. Invalid envelopes/admission fail
technically (`RESEARCH_RESPONSE_INVALID` or redacted upstream validation error).

When two successful/empty requests leave Coverage deficient, stop reason is
`exhausted`; caller-supplied open criterion gaps become `exhausted`, and the current
snapshot may be evaluated. Missing remains an evidence observation, not a negative
company fact. Independent evaluator support/applicability checks still own the
assessment partition; this slice supplies no default semantic sufficiency rule.

## Freeze and generation closure

Changed evidence requires `freeze_with_evidence`; otherwise
`RESEARCH_FREEZE_REQUIRED` blocks evaluation. This callback receives **all** admitted
Evidence and current Coverage. Its EligibilityResult copy carries current evidence
revision only as freeze context; the original eligibility classification is not
recomputed or promoted. The caller owns actual Source, Chunk, RetrievalRecord and
as-of/corpus/index metadata and retains the additional research's exact source and
record payloads for freeze. Returning Evidence alone does not establish provenance.

The frozen run/candidate/schema/policy/revision must match, and evaluation round
must be positive. Every newly admitted research Evidence must be present unchanged
in the snapshot. Existing initially collected unused items may still be omitted,
as in the original API. Every included Evidence must equal its admitted JSON DTO;
Source and related Evidence references must close. RetrievalRecord run/candidate,
ok status, timing, source/evidence/chunk links must match; Chunk source, corpus,
locator, scope, company attribution and excerpt must match. Stale revision,
omitted new Evidence, injection or broken closure fails with `SNAPSHOT_INVALID`.
The detached frozen snapshot is then subject to #24's independent generation and
branch attribution checks. Snapshot generation/closure is not live-source
allowlist/authenticity or budget approval evidence.

## Observations and zero denominator

`CandidateRunV3` adds `research_retry_count`, `research_stop_reasons`, final
`coverage_results`, and optional caller `research_gaps`. A caller trace receives
Coverage passes, charged gates, additional research, recoverable failure,
ready/exhausted stop, freeze, evaluation join, score/decision, archive and advance.
Trace records carry current counter/revision/missing/conflict observations and
`controller="python"`; they do not claim the outer loop is a LangGraph state graph.
The evaluation join separately records the actual LangGraph result.

Both Coverage `NoApplicableCriteria` and aggregate `ZeroDenominatorV3` become
`ZERO_APPLICABLE_DENOMINATOR`. No score/decision is generated. Each failed candidate
is archived and advances once, without blocking subsequent candidates.

## Remaining boundaries

Unknown eligibility remains `eligibility_unknown`, with no collection/evaluation
or automatic promotion. #82 approves additional **Evidence** requests but expressly
does not approve Company Research retry policy. Eligibility retries are therefore
a separate unresolved gap; this is not an overall live-loop/M3 completion claim.
No shared DTO/State/config/dependency, baseline controller, selector, reporting,
provider/model or corpus changes are made. #96 needs to explicitly inject and
verify the adapters; #168's live approval contracts remain gated. GitHub notices
and final full-suite/review gates are parent-owned, not claims of this slice.

Tests: `tests/integration/test_v3_research_loop.py` and `test_v3_candidates.py` use
synthetic fixture Evidence and evaluators while exercising the actual evaluation
LangGraph. RED/GREEN receipts are recorded separately in the worker scratch report.
