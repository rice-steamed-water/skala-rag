"""테스트용 fake Tool/LLM/clock. 미리 정한 출력만 돌려주고 네트워크를 쓰지 않는다.

각 fake는 호출 인자를 ``calls``에 기록한다. 준비한 출력이 떨어지면
``FakeExhausted``를 내서, 테스트가 예상보다 많이 호출한 것을 드러낸다.
"""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from skala_rag.contracts.interfaces import LLMError
from skala_rag.contracts.tools import ToolResult

DataT = TypeVar("DataT")
ModelT = TypeVar("ModelT", bound=BaseModel)


class FakeExhausted(AssertionError):
    """준비한 출력보다 많이 호출됐다."""


@dataclass(frozen=True)
class Call:
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class FakeTool(Generic[DataT]):
    """contracts §7의 어떤 ``ToolResult`` 경계든 대신한다.

    ``outputs``의 항목을 호출 순서대로 돌려준다. 예외 instance면 그 예외를 낸다.
    """

    def __init__(self, outputs: Iterable[ToolResult[DataT] | Exception]) -> None:
        self._outputs = deque(outputs)
        self.calls: list[Call] = []

    def __call__(self, *args: Any, **kwargs: Any) -> ToolResult[DataT]:
        self.calls.append(Call(args, kwargs))
        if not self._outputs:
            raise FakeExhausted(f"FakeTool called {len(self.calls)} times")
        output = self._outputs.popleft()
        if isinstance(output, Exception):
            raise output
        return output


@dataclass(frozen=True)
class LLMCall:
    system: str
    user: str
    output_schema: type[BaseModel]


class FakeLLM:
    """``StructuredLLM``. 출력은 BaseModel instance, dict payload, ``LLMError``."""

    def __init__(self, outputs: Iterable[BaseModel | dict[str, Any] | LLMError]):
        self._outputs = deque(outputs)
        self.calls: list[LLMCall] = []

    def generate(
        self, *, system: str, user: str, output_schema: type[ModelT]
    ) -> ModelT:
        self.calls.append(LLMCall(system, user, output_schema))
        if not self._outputs:
            raise FakeExhausted(f"FakeLLM called {len(self.calls)} times")
        output = self._outputs.popleft()
        if isinstance(output, LLMError):
            raise output
        return output_schema.model_validate(output)


@dataclass
class FakeClock:
    """``Clock``. ``now()``마다 ``step``만큼 진행한다."""

    current: datetime
    step: timedelta = timedelta(0)
    calls: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.current.utcoffset() is None:
            raise ValueError("FakeClock requires a timezone-aware datetime")

    def now(self) -> datetime:
        self.calls += 1
        value = self.current
        self.current += self.step
        return value

    def advance(self, delta: timedelta) -> None:
        self.current += delta
