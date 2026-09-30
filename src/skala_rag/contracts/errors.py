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
