"""Execution metadata supplied by the runner; no invented version/budget defaults."""

from typing import Self

from pydantic import StrictBool, model_validator

from .common import Contract, JSONMap, Text
from .inputs import RunInput
from .reports import ReportJudgement, ValidationResult
from .state import RunOutcome, WorkflowStatus


class ArtifactMetadata(Contract):
    artifact_path: Text
    artifact_hash: Text


class RunManifest(Contract):
    run_id: Text
    run_input: RunInput
    code_revision: Text | None = None
    uncommitted: StrictBool
    policy_version: Text
    corpus_version: Text
    prompt_versions: dict[Text, Text]
    model_versions: dict[Text, Text]
    corpus_hash: Text
    tool_status: JSONMap
    budgets: JSONMap
    usage: JSONMap
    artifacts: dict[Text, ArtifactMetadata]
    validation_results: dict[Text, ValidationResult | ReportJudgement]
    workflow_status: WorkflowStatus
    run_outcome: RunOutcome | None = None

    @model_validator(mode="after")
    def validate_metadata(self) -> Self:
        if self.code_revision is None and not self.uncommitted:
            raise ValueError("code_revision or uncommitted=True is required")
        if self.policy_version != self.run_input.policy_version:
            raise ValueError("manifest policy_version must match run_input")
        if self.corpus_version != self.run_input.corpus_version:
            raise ValueError("manifest corpus_version must match run_input")
        if (self.workflow_status == WorkflowStatus.RUNNING) != (
            self.run_outcome is None
        ):
            raise ValueError(
                "running requires no outcome; terminal status requires outcome"
            )
        return self
