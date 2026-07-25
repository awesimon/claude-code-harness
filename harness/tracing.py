"""Durable trace span lifecycle and local usage summaries."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping

from state_core import TraceSpanRecord, TraceSpanRepository, TraceSpanStatus

_ACTIVE_SPAN: ContextVar[str | None] = ContextVar("active_trace_span", default=None)


@dataclass
class TraceSpan:
    record: TraceSpanRecord
    usage: dict[str, Any] = field(default_factory=dict)
    terminal_status: TraceSpanStatus = TraceSpanStatus.COMPLETED
    error: dict[str, Any] | None = None

    @property
    def span_id(self) -> str:
        return self.record.span_id

    def set_usage(self, usage: Mapping[str, Any]) -> None:
        self.usage = dict(usage)

    def set_status(
        self,
        status: TraceSpanStatus,
        *,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        if status is TraceSpanStatus.RUNNING:
            raise ValueError("trace terminal status cannot be running")
        self.terminal_status = status
        self.error = dict(error) if error is not None else None


class TraceController:
    def __init__(
        self,
        repository: TraceSpanRepository,
        root_session_id: str,
        *,
        agent_id: str | None = None,
    ) -> None:
        self.repository = repository
        self.root_session_id = root_session_id
        self.agent_id = agent_id

    def start(
        self,
        kind: str,
        name: str,
        *,
        parent_span_id: str | None = None,
    ) -> TraceSpan:
        record = self.repository.start(
            TraceSpanRecord(
                span_id=uuid.uuid4().hex,
                root_session_id=self.root_session_id,
                agent_id=self.agent_id,
                parent_span_id=parent_span_id or _ACTIVE_SPAN.get(),
                kind=kind,
                name=name,
            )
        )
        return TraceSpan(record)

    @asynccontextmanager
    async def span(self, kind: str, name: str) -> AsyncIterator[TraceSpan]:
        span = self.start(kind, name)
        token = _ACTIVE_SPAN.set(span.span_id)
        try:
            yield span
        except asyncio.CancelledError:
            self.repository.finish(
                span.span_id,
                TraceSpanStatus.CANCELLED,
                span.record.revision,
                usage=span.usage,
            )
            raise
        except BaseException as exc:
            self.repository.finish(
                span.span_id,
                TraceSpanStatus.FAILED,
                span.record.revision,
                usage=span.usage,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            raise
        else:
            self.repository.finish(
                span.span_id,
                span.terminal_status,
                span.record.revision,
                usage=span.usage,
                error=span.error,
            )
        finally:
            _ACTIVE_SPAN.reset(token)

    def interrupt_open(self) -> list[TraceSpanRecord]:
        return self.repository.interrupt_open(self.root_session_id)

    def summary(self) -> dict[str, Any]:
        spans = self.repository.list(self.root_session_id, agent_id=self.agent_id)
        counts = {status.value: 0 for status in TraceSpanStatus}
        usage: dict[str, float] = {}
        for span in spans:
            counts[span.status.value] += 1
            for key, value in span.usage.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[key] = usage.get(key, 0.0) + float(value)
        return {**counts, "total": len(spans), "usage": usage}
