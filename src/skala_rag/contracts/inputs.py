"""Caller-supplied run input; live readiness is not schema validation."""

from typing import Literal

from .common import Contract, ISODate, Text


class RunInput(Contract):
    investment_theme: Text
    countries: list[Text]
    languages: list[Text]
    as_of: ISODate
    policy_version: Text
    corpus_version: Text
    execution_mode: Literal["fixture", "live"]
