"""Wire Generator/Judge to #45 runtime and #47/#51 single-attempt adapter."""

from skala_rag.reporting.v3_pipeline import ReportGeneratorV3, SemanticJudgeV3
from skala_rag.tools.runtime_llm import RuntimeStructuredLLM


def build_report_nodes_v3(
    *,
    runtime,
    generator_call,
    judge_call,
    budget,
    readiness,
    generator_transport,
    judge_transport,
    allowance_for,
):
    """Both roles share one caller-owned ledger; transport retry stays in runtime.

    Supply OpenAIResponsesAttempt for the approved snapshot. Its credential,
    clock/prompt/schema settings, readiness/prices/limits remain caller-owned.
    No nested transport/schema repair loop or implicit live execution is added.
    """
    if generator_call.run_id != judge_call.run_id:
        raise ValueError("Generator/Judge run mismatch")
    if generator_call.node == judge_call.node:
        raise ValueError("distinct Generator/Judge role nodes required")

    def wrapped(call, transport):
        return RuntimeStructuredLLM(
            runtime=runtime,
            call=call,
            budget=budget,
            readiness=readiness,
            transport=transport,
            allowance_for=allowance_for,
        )

    return (
        ReportGeneratorV3(wrapped(generator_call, generator_transport)),
        SemanticJudgeV3(wrapped(judge_call, judge_transport)),
    )
