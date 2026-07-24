from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from harness import SessionHarnessFactory
from state_core import SessionRuntimeFactory, SQLAlchemyStateStore, TraceSpanStatus
from state_core.sqlalchemy_store import Base


def _store(tmp_path: Path) -> SQLAlchemyStateStore:
    engine = create_engine(f"sqlite:///{tmp_path / 'traces.db'}")
    Base.metadata.create_all(engine)
    return SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))


@pytest.mark.asyncio
async def test_nested_spans_finish_with_usage_and_parent(tmp_path: Path) -> None:
    from harness.tracing import TraceController

    store = _store(tmp_path)
    traces = TraceController(store.traces, "root", agent_id="a1")
    async with traces.span("agent", "review") as root:
        async with traces.span("tool", "read_file") as child:
            child.set_usage({"tokens": 3})

    persisted_root = store.traces.get(root.span_id)
    persisted_child = store.traces.get(child.span_id)
    assert persisted_root.status is TraceSpanStatus.COMPLETED
    assert persisted_child.status is TraceSpanStatus.COMPLETED
    assert persisted_child.parent_span_id == persisted_root.span_id
    assert persisted_child.usage == {"tokens": 3}


@pytest.mark.asyncio
async def test_span_failure_is_durable_and_preserves_exception(tmp_path: Path) -> None:
    from harness.tracing import TraceController

    store = _store(tmp_path)
    traces = TraceController(store.traces, "root")
    with pytest.raises(RuntimeError, match="broken"):
        async with traces.span("model", "completion") as span:
            raise RuntimeError("broken Authorization: Bearer secret")

    persisted = store.traces.get(span.span_id)
    assert persisted.status is TraceSpanStatus.FAILED
    assert "secret" not in str(persisted.error)


def test_resume_interrupts_open_spans_and_exposes_summary(tmp_path: Path) -> None:
    store = _store(tmp_path)
    factory = SessionHarnessFactory(
        SessionRuntimeFactory(store), workspace_root=tmp_path
    )
    harness = factory.create("root")
    opened = harness.traces.start("tool", "bash")

    resumed = factory.resume("root")
    summary = resumed.traces.summary()

    assert store.traces.get(opened.span_id).status is TraceSpanStatus.INTERRUPTED
    assert summary["interrupted"] == 1
