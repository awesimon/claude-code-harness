"""Projected context control with durable, auditable compaction boundaries."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping, Sequence

from services.compact.context_compactor import (
    ContextCompactor,
    micro_compact_messages,
)
from services.llm_service import Message
from state_core import EventType, RuntimeRecordRevisionConflict, SessionEvent

from .budget import BudgetKind
from .hooks import HookContext, HookDecision

if TYPE_CHECKING:
    from .session import SessionHarness


COMPACTION_NAMESPACE = "context.compaction"
COMPACTION_VERSION = 1
_TRANSCRIPT_EVENTS = frozenset(
    {
        EventType.USER_MESSAGE,
        EventType.ASSISTANT_MESSAGE,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
    }
)


@dataclass(frozen=True)
class ContextControlConfig:
    micro_threshold_tokens: int = 3_200
    hard_threshold_tokens: int = 4_000
    target_tokens: int = 2_800

    def __post_init__(self) -> None:
        values = (
            self.micro_threshold_tokens,
            self.hard_threshold_tokens,
            self.target_tokens,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values
        ):
            raise ValueError("context token thresholds must be positive integers")
        if self.micro_threshold_tokens > self.hard_threshold_tokens:
            raise ValueError("micro threshold must not exceed hard threshold")
        if self.target_tokens > self.micro_threshold_tokens:
            raise ValueError("target tokens must not exceed the micro threshold")


@dataclass(frozen=True)
class CompactionSummary:
    summary: str
    usage: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("compaction summary must be non-empty")
        object.__setattr__(self, "usage", dict(self.usage))


class ContextCompactionError(RuntimeError):
    """A classified compaction failure that leaves the prior boundary intact."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "context_compaction_failed",
        cause: BaseException | None = None,
    ) -> None:
        self.category = category
        self.retryable = False
        self.cause = cause
        super().__init__(message)


SummaryCallback = Callable[[Sequence[Message]], Awaitable[str | CompactionSummary]]


class ContextController:
    """Build model context from raw events without rewriting the transcript."""

    def __init__(
        self,
        harness: "SessionHarness",
        *,
        config: ContextControlConfig | None = None,
        summarize: SummaryCallback | None = None,
        compactor: ContextCompactor | None = None,
        hooks: Any = None,
    ) -> None:
        self.harness = harness
        self.config = config or ContextControlConfig()
        self.summarize = summarize
        self.compactor = compactor or ContextCompactor(
            max_tokens=self.config.micro_threshold_tokens
        )
        self.hooks = harness.hooks if hooks is None else hooks

    def restore_messages(self) -> list[Message]:
        events = self._transcript_events()
        boundary = self._valid_boundary(events)
        if boundary is None:
            return self._project_events(events)
        later = [event for event in events if event.id > boundary["through_event_id"]]
        return [Message(role="user", content=boundary["summary"]), *self._project_events(later)]

    async def prepare_messages(self, messages: Sequence[Message]) -> list[Message]:
        system_messages = [message for message in messages if message.role == "system"]
        transcript_messages = self.restore_messages()
        projected = [*system_messages, *transcript_messages]
        if not transcript_messages:
            projected = list(messages)

        token_count = self.compactor._estimate_tokens(projected)
        if token_count >= self.config.hard_threshold_tokens:
            return await self._hard_compact(projected, system_messages, token_count)
        if token_count >= self.config.micro_threshold_tokens:
            result = micro_compact_messages(
                projected,
                target_tokens=self.config.target_tokens,
                compactor=self.compactor,
            )
            if result.success:
                return list(result.compressed_messages)
        return projected

    async def _hard_compact(
        self,
        messages: list[Message],
        system_messages: list[Message],
        token_count: int,
    ) -> list[Message]:
        transcript_events = self._transcript_events()
        if not transcript_events:
            return list(messages)
        through_event_id = transcript_events[-1].id
        hook_context = HookContext(
            session_id=self.harness.root_session_id,
            agent_id=self.harness.agent_id,
            cwd=self.harness.effective_cwd,
            cancellation=self.harness.runtime_context.cancellation,
        )
        details = {
            "trigger": "auto",
            "token_count": token_count,
            "through_event_id": through_event_id,
            "source_event_count": len(transcript_events),
        }
        pre = await self.hooks.run_pre_compact(details, hook_context)
        if pre is not None and getattr(pre, "decision", HookDecision.ALLOW) is HookDecision.BLOCK:
            raise ContextCompactionError(
                getattr(pre, "reason", None) or "compaction blocked by hook",
                category="context_compaction_blocked",
            )

        reservation = self.harness.budget.reserve(
            BudgetKind.COMPACTION_TOKENS,
            max(1, token_count),
            agent_id=self.harness.agent_id,
        )
        try:
            if self.summarize is None:
                raise ContextCompactionError("no compaction summary callback is configured")
            async with self.harness.traces.span("model", "context_compaction") as span:
                value = await self.summarize(tuple(messages))
                summary = (
                    value if isinstance(value, CompactionSummary) else CompactionSummary(value)
                )
                span.set_usage(summary.usage)
        except Exception as exc:
            reservation.release()
            if isinstance(exc, ContextCompactionError):
                raise
            raise ContextCompactionError(f"context compaction failed: {exc}", cause=exc) from exc
        except BaseException:
            reservation.release()
            raise

        actual_tokens = self._usage_tokens(summary.usage, token_count)
        reservation.consume(actual_tokens)
        snapshot = self._boundary_snapshot(
            summary.summary,
            through_event_id,
            len(transcript_events),
        )
        self._persist_boundary(snapshot)
        await self.hooks.run_post_compact(
            {**details, "summary_digest": snapshot["summary_digest"]},
            hook_context,
        )
        return [*system_messages, Message(role="user", content=summary.summary)]

    def _transcript_events(self) -> list[SessionEvent]:
        return [
            event
            for event in self.harness.session_runtime.events()
            if event.event_type in _TRANSCRIPT_EVENTS
        ]

    def _valid_boundary(self, events: Sequence[SessionEvent]) -> dict[str, Any] | None:
        record = self.harness.store.metadata.get(self.harness.root_session_id, COMPACTION_NAMESPACE)
        if record is None:
            return None
        snapshot = dict(record.snapshot)
        version = snapshot.get("version")
        through_event_id = snapshot.get("through_event_id")
        source_event_count = snapshot.get("source_event_count")
        summary = snapshot.get("summary")
        digest = snapshot.get("summary_digest")
        created_at = snapshot.get("created_at")
        if version != COMPACTION_VERSION:
            return None
        if (
            isinstance(through_event_id, bool)
            or not isinstance(through_event_id, int)
            or through_event_id <= 0
        ):
            return None
        if (
            isinstance(source_event_count, bool)
            or not isinstance(source_event_count, int)
            or source_event_count <= 0
        ):
            return None
        if not isinstance(summary, str) or not summary.strip():
            return None
        if not isinstance(digest, str) or not hmac.compare_digest(
            digest, self._summary_digest(summary)
        ):
            return None
        if not isinstance(created_at, str):
            return None
        try:
            datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        through_index = next(
            (index for index, event in enumerate(events) if event.id == through_event_id),
            None,
        )
        if through_index is None or source_event_count != through_index + 1:
            return None
        return snapshot

    def _persist_boundary(self, snapshot: Mapping[str, Any]) -> None:
        current = self.harness.store.metadata.get(
            self.harness.root_session_id, COMPACTION_NAMESPACE
        )
        expected_revision = current.revision if current is not None else None
        try:
            self.harness.store.metadata.put(
                self.harness.root_session_id,
                COMPACTION_NAMESPACE,
                snapshot,
                expected_revision,
            )
        except RuntimeRecordRevisionConflict as exc:
            raise ContextCompactionError(
                "context boundary changed during compaction",
                category="context_compaction_conflict",
                cause=exc,
            ) from exc

    @classmethod
    def _boundary_snapshot(
        cls, summary: str, through_event_id: int, source_event_count: int
    ) -> dict[str, Any]:
        return {
            "version": COMPACTION_VERSION,
            "through_event_id": through_event_id,
            "summary": summary,
            "summary_digest": cls._summary_digest(summary),
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_event_count": source_event_count,
        }

    @staticmethod
    def _summary_digest(summary: str) -> str:
        return hashlib.sha256(summary.encode("utf-8")).hexdigest()

    @staticmethod
    def _usage_tokens(usage: Mapping[str, Any], fallback: int) -> int:
        value = usage.get("total_tokens")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return max(1, int(value))
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
        if all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in (input_tokens, output_tokens)
        ):
            total = input_tokens + output_tokens
            if total > 0:
                return max(1, int(total))
        return max(1, fallback)

    @staticmethod
    def _project_events(events: Sequence[SessionEvent]) -> list[Message]:
        messages: list[Message] = []
        for event in events:
            payload = event.payload
            if event.event_type is EventType.USER_MESSAGE:
                messages.append(Message(role="user", content=str(payload.get("content") or "")))
            elif event.event_type is EventType.ASSISTANT_MESSAGE:
                messages.append(
                    Message(role="assistant", content=str(payload.get("content") or ""))
                )
            elif event.event_type is EventType.TOOL_CALL:
                if not messages or messages[-1].role != "assistant":
                    messages.append(Message(role="assistant", content=""))
                arguments = payload.get("input") or {}
                try:
                    encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
                except (TypeError, ValueError):
                    encoded = "{}"
                tool_calls = list(messages[-1].tool_calls or [])
                tool_calls.append(
                    {
                        "id": str(payload.get("toolCallId") or ""),
                        "type": "function",
                        "function": {
                            "name": str(payload.get("name") or ""),
                            "arguments": encoded,
                        },
                    }
                )
                messages[-1].tool_calls = tool_calls
            elif event.event_type is EventType.TOOL_RESULT:
                result = payload.get("result")
                if not isinstance(result, str):
                    try:
                        result = json.dumps(
                            result, ensure_ascii=False, default=str, allow_nan=False
                        )
                    except (TypeError, ValueError):
                        result = str(result)
                messages.append(
                    Message(
                        role="tool",
                        content=result,
                        tool_call_id=str(payload.get("toolCallId") or ""),
                        name=str(payload.get("name") or ""),
                    )
                )
        return messages
