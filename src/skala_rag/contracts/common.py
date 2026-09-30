"""Shared structural types, without policy defaults or external I/O."""

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationInfo,
)


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must be nonblank")
    return value


def date_only(value: object) -> object:
    if type(value) is date:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return date.fromisoformat(value)
    raise ValueError("expected ISO date YYYY-MM-DD, not a timestamp")


Text = Annotated[StrictStr, AfterValidator(nonblank)]
ISODate = Annotated[date, BeforeValidator(date_only)]
Number = Annotated[StrictInt | StrictFloat, Field(allow_inf_nan=False)]
Count = Annotated[StrictInt, Field(ge=0)]
Confidence = Literal["high", "medium", "low", "unknown"]


def explicit_timestamp(value: object) -> object:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and "T" in value:
        return value
    raise ValueError("expected explicit ISO timestamp, not an epoch number")


Timestamp = Annotated[AwareDatetime, BeforeValidator(explicit_timestamp)]


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_default=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )
    schema_version: Text


class MonetaryObservation(Contract):
    value: Number
    currency: Text
    unit: Text
    as_of: ISODate


JSONMap = dict[Text, JsonValue]


def fixture_locator(value: str, info: ValidationInfo) -> str:
    if value.strip().lower().startswith("fixture://") and (
        not isinstance(info.context, dict)
        or info.context.get("execution_mode") != "fixture"
    ):
        raise ValueError("fixture:// requires explicit fixture validation context")
    return value


Locator = Annotated[Text, AfterValidator(fixture_locator)]
