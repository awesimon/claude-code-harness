from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from harness import SessionHarnessFactory
from harness.budget import BudgetExhausted, BudgetKind
from harness.context_control import (
    COMPACTION_NAMESPACE,
    CompactionSummary,
    ContextCompactionError,
    ContextControlConfig,
    ContextController,
)
from query_engine import QueryEngine
from services.llm_service import ChatCompletionResponse, Message
from state_core import EventType, SessionRuntimeFactory, SQLAlchemyStateStore
from state_core.sqlalchemy_store import Base


def _store(tmp_path: Path, name: str = "context.db") -> SQLAlchemyStateStore:
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))


def _harness(tmp_path: Path, session_id: str = "root"):
    store = _store(tmp_path)
    harness = SessionHarnessFactory(SessionRuntimeFactory(store), workspace_root=tmp_path).create(
        session_id
    )
    return harness, store


def _message_contents(messages: list[Message]) -> list[str]:
    return [message.content for message in messages]


@pytest.mark.asyncio
async def test_micro_compaction_changes_projection_without_changing_raw_events(
    tmp_path: Path,
) -> None:
    harness, _ = _harness(tmp_path)
    runtime = harness.session_runtime
    original = "old-output " * 300
    runtime.append_event(EventType.USER_MESSAGE, {"content": "inspect"})
    runtime.append_event(EventType.ASSISTANT_MESSAGE, {"content": original})
    raw_before = [event.to_dict() for event in runtime.events()]
    controller = ContextController(
        harness,
        config=ContextControlConfig(
            micro_threshold_tokens=20,
            hard_threshold_tokens=10_000,
            target_tokens=15,
        ),
    )

    projected = await controller.prepare_messages(
        [
            Message(role="system", content="system"),
            Message(role="user", content="inspect"),
            Message(role="assistant", content=original),
        ]
    )

    assert _message_contents(projected) != ["system", "inspect", original]
    assert [event.to_dict() for event in runtime.events()] == raw_before
    assert harness.store.metadata.get(harness.root_session_id, COMPACTION_NAMESPACE) is None


class _RecordingHooks:
    def __init__(self, order: list[str], store, session_id: str) -> None:
        self.order = order
        self.store = store
        self.session_id = session_id

    async def run_pre_compact(self, details, context):
        self.order.append("pre")

    async def run_post_compact(self, details, context):
        assert self.store.metadata.get(self.session_id, COMPACTION_NAMESPACE) is not None
        self.order.append("post")


@pytest.mark.asyncio
async def test_hard_threshold_runs_hooks_reserves_budget_and_persists_before_post(
    tmp_path: Path,
) -> None:
    harness, store = _harness(tmp_path)
    runtime = harness.session_runtime
    runtime.append_event(EventType.USER_MESSAGE, {"content": "old " * 100})
    order: list[str] = []

    async def summarize(messages):
        order.append("summary")
        assert "old" in messages[-1].content
        return CompactionSummary("durable summary", {"total_tokens": 7})

    controller = ContextController(
        harness,
        config=ContextControlConfig(
            micro_threshold_tokens=5,
            hard_threshold_tokens=10,
            target_tokens=5,
        ),
        summarize=summarize,
        hooks=_RecordingHooks(order, store, harness.root_session_id),
    )

    messages = await controller.prepare_messages(
        [Message(role="system", content="system"), Message(role="user", content="old " * 100)]
    )

    assert order == ["pre", "summary", "post"]
    assert _message_contents(messages) == ["system", "durable summary"]
    boundary = store.metadata.get(harness.root_session_id, COMPACTION_NAMESPACE)
    assert boundary is not None
    assert boundary.snapshot["through_event_id"] == next(
        event.id
        for event in reversed(runtime.events())
        if event.event_type is EventType.USER_MESSAGE
    )
    assert boundary.snapshot["summary_digest"] == hashlib.sha256(b"durable summary").hexdigest()
    assert harness.budget.usage()[BudgetKind.COMPACTION_TOKENS] == 7
    assert [event.event_type for event in runtime.events()][-2:] == [
        EventType.COMPACTION_STARTED,
        EventType.COMPACTION_BOUNDARY,
    ]


@pytest.mark.asyncio
async def test_durable_restore_uses_summary_and_later_raw_transcript(tmp_path: Path) -> None:
    harness, store = _harness(tmp_path)
    runtime = harness.session_runtime
    runtime.append_event(EventType.USER_MESSAGE, {"content": "old " * 100})

    async def summarize(_messages):
        return "summary"

    controller = ContextController(
        harness,
        config=ContextControlConfig(5, 10, 5),
        summarize=summarize,
    )
    await controller.prepare_messages([Message(role="user", content="old " * 100)])
    runtime.append_event(EventType.ASSISTANT_MESSAGE, {"content": "new"})

    resumed = SessionHarnessFactory(SessionRuntimeFactory(store), workspace_root=tmp_path).resume(
        harness.root_session_id
    )
    restored = ContextController(resumed).restore_messages()

    assert _message_contents(restored) == ["summary", "new"]


def test_invalid_boundary_digest_falls_back_to_full_raw_transcript(tmp_path: Path) -> None:
    harness, store = _harness(tmp_path)
    runtime = harness.session_runtime
    runtime.append_event(EventType.USER_MESSAGE, {"content": "old"})
    runtime.append_event(EventType.ASSISTANT_MESSAGE, {"content": "new"})
    store.metadata.put(
        harness.root_session_id,
        COMPACTION_NAMESPACE,
        {
            "version": 1,
            "through_event_id": runtime.events()[0].id,
            "summary": "tampered",
            "summary_digest": "invalid",
            "created_at": "2026-07-25T00:00:00Z",
            "source_event_count": 1,
        },
    )

    restored = ContextController(harness).restore_messages()

    assert _message_contents(restored) == ["old", "new"]


@pytest.mark.asyncio
async def test_summary_failure_preserves_prior_valid_boundary(tmp_path: Path) -> None:
    harness, store = _harness(tmp_path)
    runtime = harness.session_runtime
    runtime.append_event(EventType.USER_MESSAGE, {"content": "first " * 100})

    async def first_summary(_messages):
        return "first summary"

    config = ContextControlConfig(5, 10, 5)
    await ContextController(harness, config=config, summarize=first_summary).prepare_messages(
        [Message(role="user", content="first " * 100)]
    )
    prior = store.metadata.get(harness.root_session_id, COMPACTION_NAMESPACE)
    runtime.append_event(EventType.USER_MESSAGE, {"content": "second " * 100})

    async def fail_summary(_messages):
        raise RuntimeError("summary unavailable")

    with pytest.raises(ContextCompactionError) as caught:
        await ContextController(harness, config=config, summarize=fail_summary).prepare_messages(
            [
                Message(role="user", content="first " * 100),
                Message(role="user", content="second " * 100),
            ]
        )

    current = store.metadata.get(harness.root_session_id, COMPACTION_NAMESPACE)
    assert caught.value.category == "context_compaction_failed"
    assert current == prior
    assert runtime.events()[-1].event_type is EventType.COMPACTION_FAILED
    assert runtime.events()[-1].payload["category"] == "context_compaction_failed"
    assert _message_contents(ContextController(harness).restore_messages()) == [
        "first summary",
        "second " * 100,
    ]


@pytest.mark.asyncio
async def test_compaction_budget_exhaustion_skips_summary_and_boundary(tmp_path: Path) -> None:
    harness, store = _harness(tmp_path)
    harness.session_runtime.append_event(EventType.USER_MESSAGE, {"content": "large " * 100})
    harness.budget.configure({BudgetKind.COMPACTION_TOKENS: 1})
    called = False

    async def summarize(_messages):
        nonlocal called
        called = True
        return "summary"

    controller = ContextController(
        harness,
        config=ContextControlConfig(5, 10, 5),
        summarize=summarize,
    )

    with pytest.raises(BudgetExhausted):
        await controller.prepare_messages([Message(role="user", content="large " * 100)])

    assert called is False
    assert store.metadata.get(harness.root_session_id, COMPACTION_NAMESPACE) is None


class _RecordingCompletion:
    def __init__(self, *, streaming: bool) -> None:
        self.streaming = streaming
        self.requests = []

    async def chat_completion(self, request):
        self.requests.append(request)
        return ChatCompletionResponse(
            id="response",
            model="test",
            content="done",
            finish_reason="stop",
            usage={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        )

    async def chat_completion_stream(self, request):
        self.requests.append(request)
        yield ChatCompletionResponse(
            id="response",
            model="test",
            content="done",
            finish_reason="stop",
            usage={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["chat", "chat_stream"])
async def test_query_engine_uses_shared_context_controller_and_records_usage(
    tmp_path: Path, method: str
) -> None:
    store = _store(tmp_path, f"{method}.db")
    llm = _RecordingCompletion(streaming=method == "chat_stream")

    async def summarize(_messages):
        return CompactionSummary("query summary", {"total_tokens": 2})

    engine = QueryEngine(
        llm_service=llm,
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=SessionRuntimeFactory(store),
        context_control_config=ContextControlConfig(1, 2, 1),
        context_summary_callback=summarize,
    )
    engine.create_conversation(method)

    events = [event async for event in getattr(engine, method)(method, "large prompt " * 20)]

    assert not any(event["type"] == "error" for event in events)
    assert _message_contents(llm.requests[0].messages)[-1] == "query summary"
    expected = 5 if method == "chat" else 6
    harness = engine._session_harness(method)
    assert harness.budget.usage()[BudgetKind.TOTAL_TOKENS] == expected
    assert harness.budget.usage()[BudgetKind.COMPACTION_TOKENS] == 2
    assert harness.traces.summary()["usage"]["total_tokens"] == expected + 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["chat", "chat_stream"])
async def test_query_engine_classifies_compaction_failure_before_model_call(
    tmp_path: Path, method: str
) -> None:
    store = _store(tmp_path, f"failure-{method}.db")
    llm = _RecordingCompletion(streaming=method == "chat_stream")

    async def fail_summary(_messages):
        raise RuntimeError("summary unavailable")

    engine = QueryEngine(
        llm_service=llm,
        enable_error_recovery=False,
        workspace_root=tmp_path,
        session_runtime_factory=SessionRuntimeFactory(store),
        context_control_config=ContextControlConfig(1, 2, 1),
        context_summary_callback=fail_summary,
    )
    engine.create_conversation(f"failure-{method}")

    events = [
        event async for event in getattr(engine, method)(f"failure-{method}", "large prompt " * 20)
    ]

    assert events[-1]["type"] == "error"
    assert events[-1]["error_category"] == "context_compaction_failed"
    assert llm.requests == []
