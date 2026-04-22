"""Structural types for the Web Serial API as seen from Pyodide."""

# ruff: noqa: D102, N815

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Protocol, TypedDict

from typing_extensions import NotRequired, Unpack


class SerialOptions(TypedDict):
    """`SerialOptions` dictionary passed to `SerialPort.open`."""

    baudRate: int
    dataBits: NotRequired[int]
    stopBits: NotRequired[int]
    parity: NotRequired[str]
    bufferSize: NotRequired[int]
    flowControl: NotRequired[str]


# `break` is a Python keyword; we must use TypedDict's functional form.
SerialOutputSignals = TypedDict(
    "SerialOutputSignals",
    {
        "requestToSend": NotRequired[bool],
        "dataTerminalReady": NotRequired[bool],
        "break": NotRequired[bool],
    },
)


class JsStreamReader(Protocol):
    """`ReadableStreamDefaultReader`."""

    def read(self) -> Awaitable[Any]: ...
    def releaseLock(self) -> None: ...


class JsStreamWriter(Protocol):
    """`WritableStreamDefaultWriter`."""

    def write(self, data: Any) -> Awaitable[None]: ...
    def releaseLock(self) -> None: ...


class JsReadableStream(Protocol):
    """`ReadableStream`."""

    def getReader(self) -> JsStreamReader: ...


class JsWritableStream(Protocol):
    """`WritableStream`."""

    def getWriter(self) -> JsStreamWriter: ...


class JsSerialInputSignals(Protocol):
    """`SerialInputSignals`."""

    clearToSend: bool
    dataCarrierDetect: bool
    ringIndicator: bool
    dataSetReady: bool


class JsSerialPort(Protocol):
    """`SerialPort`."""

    readable: JsReadableStream
    writable: JsWritableStream

    def open(self, **kwargs: Unpack[SerialOptions]) -> Awaitable[None]: ...
    def close(self) -> Awaitable[None]: ...
    def getSignals(self) -> Awaitable[JsSerialInputSignals]: ...
    def setSignals(self, **kwargs: Unpack[SerialOutputSignals]) -> Awaitable[None]: ...
