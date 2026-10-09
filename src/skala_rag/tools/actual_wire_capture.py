"""정확한 wire(요청 bytes·status·응답 bytes) 캡처와 occurrence 단위 replay.

``OpenAIResponsesAttempt.observe_wire``가 넘기는 ``(request bytes, status, response
bytes)``를 role별 호출 순서대로 보존하고, 저장·적재 후 정확한 ``httpx.MockTransport``로
같은 순서대로 되돌려준다. 이 모듈은 의미상 승인을 주지 않는다: 실제 origin 검증과
외부 trusted pin은 호출자(runner) 몫이다. credential·header·원본 예외는 저장하지 않으며
통제된 wire로 만든 capture는 실제 실행의 증거가 아니다.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, ValidationError

SCHEMA_VERSION = "actual-wire-capture-v1"
MAX_RECORDS = 40
MAX_ROLE_KEY_CHARS = 256
MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024


class WireCaptureError(ValueError):
    """고정 메시지만 가진 오류. 원본 예외·payload·header를 싣지 않는다."""


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class WireRecord(_Strict):
    role_key: StrictStr
    occurrence: StrictInt
    request_b64: StrictStr
    status: StrictInt
    response_b64: StrictStr
    sha256: StrictStr


class WireCaptureFile(_Strict):
    schema_version: StrictStr
    records: tuple[WireRecord, ...]


def _check_role(role_key: object) -> str:
    if (
        type(role_key) is not str
        or not role_key
        or role_key != role_key.strip()
        or len(role_key) > MAX_ROLE_KEY_CHARS
    ):
        raise WireCaptureError("invalid role key")
    return role_key


def _record_hash(
    role_key: str, occurrence: int, request: bytes, status: int, response: bytes
) -> str:
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            [SCHEMA_VERSION, role_key, occurrence, status, len(request), len(response)],
            separators=(",", ":"),
        ).encode()
    )
    digest.update(request)
    digest.update(response)
    return digest.hexdigest()


def _decode(value: str) -> bytes:
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError):
        raise WireCaptureError("invalid wire encoding") from None
    if base64.b64encode(raw).decode("ascii") != value:
        raise WireCaptureError("invalid wire encoding")
    return raw


def _make_record(
    role_key: str, occurrence: int, request: bytes, status: int, response: bytes
) -> WireRecord:
    return WireRecord(
        role_key=role_key,
        occurrence=occurrence,
        request_b64=base64.b64encode(request).decode("ascii"),
        status=status,
        response_b64=base64.b64encode(response).decode("ascii"),
        sha256=_record_hash(role_key, occurrence, request, status, response),
    )


def _record_ok(record: WireRecord) -> bool:
    try:
        _check_role(record.role_key)
        request = _decode(record.request_b64)
        response = _decode(record.response_b64)
    except WireCaptureError:
        return False
    if not 100 <= record.status <= 599 or record.occurrence < 0:
        return False
    if len(request) > MAX_RECORD_BYTES or len(response) > MAX_RECORD_BYTES:
        return False
    expected = _record_hash(
        record.role_key, record.occurrence, request, record.status, response
    )
    return hmac.compare_digest(expected, record.sha256)


def _serialize(records: tuple[WireRecord, ...]) -> bytes:
    document = WireCaptureFile(schema_version=SCHEMA_VERSION, records=records)
    return json.dumps(
        document.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _closure_ok(records: tuple[WireRecord, ...], max_records: int) -> bool:
    if not 0 < len(records) <= max_records:
        return False
    next_occurrence: dict[str, int] = {}
    total = 0
    for record in records:
        if not _record_ok(record):
            return False
        if record.occurrence != next_occurrence.get(record.role_key, 0):
            return False
        next_occurrence[record.role_key] = record.occurrence + 1
        total += len(record.request_b64) + len(record.response_b64)
    return total <= MAX_TOTAL_BYTES * 2


class ActualWireCapture:
    """role별 wire 기록. 기록 모드(생성자)와 replay 모드(``load``)가 있다."""

    def __init__(self, *, max_records: int = MAX_RECORDS) -> None:
        if type(max_records) is not int or not 0 < max_records <= MAX_RECORDS:
            raise WireCaptureError("invalid record bound")
        self._max_records = max_records
        self._lock = threading.Lock()
        self._records: list[WireRecord] = []
        self._counts: dict[str, int] = {}
        self._total_bytes = 0
        self._saved = False
        self._replay = False
        self._pinned_bytes: bytes | None = None
        self._pin: str | None = None
        self._cursor: dict[str, int] = {}
        self._violations = 0

    def observe(self, role_key: str) -> Callable[[bytes, int, bytes], None]:
        role = _check_role(role_key)

        def observer(request: bytes, status: int, response: bytes) -> None:
            self._append(role, request, status, response)

        return observer

    def _append(self, role: str, request: object, status: object, response: object):
        if type(request) is not bytes or type(response) is not bytes:
            raise WireCaptureError("wire must be bytes")
        if type(status) is not int or not 100 <= status <= 599:
            raise WireCaptureError("invalid status")
        if len(request) > MAX_RECORD_BYTES or len(response) > MAX_RECORD_BYTES:
            raise WireCaptureError("wire too large")
        with self._lock:
            if self._replay or self._saved:
                raise WireCaptureError("capture is closed")
            if len(self._records) >= self._max_records:
                raise WireCaptureError("capture is full")
            if self._total_bytes + len(request) + len(response) > MAX_TOTAL_BYTES:
                raise WireCaptureError("capture is full")
            occurrence = self._counts.get(role, 0)
            self._records.append(
                _make_record(role, occurrence, request, status, response)
            )
            self._counts[role] = occurrence + 1
            self._total_bytes += len(request) + len(response)

    def save(self, path: Path) -> str:
        """한 번만 저장하고 기존 파일은 보존한다. 저장 bytes의 SHA256을 반환한다."""
        with self._lock:
            if self._replay or self._saved:
                raise WireCaptureError("capture already committed")
            records = tuple(self._records)
            if not _closure_ok(records, self._max_records):
                raise WireCaptureError("nothing to save")
            payload = _serialize(records)
            target = Path(path)
            temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                with open(temp, "xb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.link(temp, target)
            except OSError:
                raise WireCaptureError("save failed") from None
            finally:
                temp.unlink(missing_ok=True)
            self._saved = True
            self._pinned_bytes = payload
            self._pin = hashlib.sha256(payload).hexdigest()
            return self._pin

    @classmethod
    def load(cls, path: Path, expected_sha256: str) -> Self:
        """외부 pin과 모든 record closure를 확인한 replay 전용 capture."""
        if (
            type(expected_sha256) is not str
            or len(expected_sha256) != 64
            or expected_sha256 != expected_sha256.lower()
        ):
            raise WireCaptureError("invalid pin")
        try:
            payload = Path(path).read_bytes()
        except OSError:
            raise WireCaptureError("load failed") from None
        actual = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual, expected_sha256):
            raise WireCaptureError("pin mismatch")
        try:
            document = WireCaptureFile.model_validate_json(payload, strict=True)
        except ValidationError:
            raise WireCaptureError("invalid capture") from None
        records = document.records
        if (
            document.schema_version != SCHEMA_VERSION
            or not _closure_ok(records, MAX_RECORDS)
            or _serialize(records) != payload
        ):
            raise WireCaptureError("invalid capture")
        capture = cls(max_records=MAX_RECORDS)
        capture._records = list(records)
        for record in records:
            capture._counts[record.role_key] = record.occurrence + 1
        capture._replay = True
        capture._pinned_bytes = payload
        capture._pin = actual
        return capture

    def verify(self) -> bool:
        """소비하지 않고 pin된 bytes와 모든 record hash를 다시 확인한다."""
        with self._lock:
            records = tuple(self._records)
            if not _closure_ok(records, self._max_records):
                return False
            if self._pinned_bytes is None:
                return True
            if self._pin is None or not hmac.compare_digest(
                hashlib.sha256(self._pinned_bytes).hexdigest(), self._pin
            ):
                return False
            return _serialize(records) == self._pinned_bytes

    def replay_transport(self, role_key: str) -> httpx.MockTransport:
        role = _check_role(role_key)
        if not self._replay or not self.verify():
            raise WireCaptureError("replay requires a verified loaded capture")
        queue = [r for r in self._records if r.role_key == role]
        if not queue:
            raise WireCaptureError("unknown role")

        def handler(request: httpx.Request) -> httpx.Response:
            with self._lock:
                index = self._cursor.get(role, 0)
                if index >= len(queue):
                    self._violations += 1
                    raise WireCaptureError("role exhausted")
                record = queue[index]
                if request.content != _decode(record.request_b64):
                    self._violations += 1
                    raise WireCaptureError("request mismatch")
                self._cursor[role] = index + 1
                return httpx.Response(
                    record.status, content=_decode(record.response_b64)
                )

        return httpx.MockTransport(handler)

    def assert_consumed(self) -> None:
        with self._lock:
            if not self._replay:
                raise WireCaptureError("replay requires a loaded capture")
            if self._violations:
                raise WireCaptureError("replay violation")
            if any(
                self._cursor.get(role, 0) != count
                for role, count in self._counts.items()
            ):
                raise WireCaptureError("unconsumed records")
