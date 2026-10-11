"""Redacted workflow errors supplied by wrappers, without retry policy."""

from pydantic import StrictBool

from .common import Contract, Count, Text, Timestamp


class WorkflowError(Contract):
    error_id: Text
    run_id: Text
    candidate_id: Text | None = None
    node: Text
    error_code: Text
    message_redacted: Text
    retryable: StrictBool
    attempt: Count
    timestamp: Timestamp


class StageFailure(Exception):
    """A stage's already-redacted terminal errors, not missing observations."""

    def __init__(self, errors: list[WorkflowError]):
        if not errors:
            raise ValueError("StageFailure requires at least one WorkflowError")
        super().__init__("Candidate graph stage failed")
        self.errors = errors
