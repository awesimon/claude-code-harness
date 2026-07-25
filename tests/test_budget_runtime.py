from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from harness import CancellationToken
from state_core import SQLAlchemyStateStore
from state_core.sqlalchemy_store import Base


def _store(tmp_path: Path) -> SQLAlchemyStateStore:
    engine = create_engine(f"sqlite:///{tmp_path / 'budget.db'}")
    Base.metadata.create_all(engine)
    return SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))


def test_concurrent_reservations_cannot_exceed_root_budget(tmp_path: Path) -> None:
    from harness.budget import BudgetController, BudgetExhausted, BudgetKind

    store = _store(tmp_path)
    BudgetController(store.metadata, "root").configure({BudgetKind.TOOL_CALLS: 1})
    barrier = threading.Barrier(2)

    def reserve(agent_id: str) -> str:
        controller = BudgetController(store.metadata, "root")
        barrier.wait()
        try:
            controller.reserve(BudgetKind.TOOL_CALLS, 1, agent_id=agent_id)
            return "reserved"
        except BudgetExhausted:
            return "exhausted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ("a1", "a2")))

    assert sorted(results) == ["exhausted", "reserved"]


@pytest.mark.parametrize(
    "kind",
    [
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cost",
        "model_turns",
        "tool_calls",
        "wall_clock",
        "compaction_tokens",
    ],
)
def test_every_budget_dimension_reserves_consumes_and_restores(
    tmp_path: Path, kind: str
) -> None:
    from harness.budget import BudgetController, BudgetExhausted, BudgetKind

    store = _store(tmp_path)
    dimension = BudgetKind(kind)
    controller = BudgetController(store.metadata, "root")
    controller.configure({dimension: 2}, agent_id="a1")
    reservation = controller.reserve(dimension, 1, agent_id="a1")
    reservation.consume(1)

    resumed = BudgetController(store.metadata, "root")
    assert resumed.usage(agent_id="a1")[dimension] == 1
    with pytest.raises(BudgetExhausted) as caught:
        resumed.reserve(dimension, 2, agent_id="a1")
    assert caught.value.kind is dimension
    assert caught.value.scope == "a1"


def test_child_consumption_rolls_up_and_release_restores_capacity(tmp_path: Path) -> None:
    from harness.budget import BudgetController, BudgetKind

    store = _store(tmp_path)
    controller = BudgetController(store.metadata, "root")
    controller.configure({BudgetKind.TOOL_CALLS: 2})
    controller.configure({BudgetKind.TOOL_CALLS: 1}, agent_id="a1")
    first = controller.reserve(BudgetKind.TOOL_CALLS, 1, agent_id="a1")
    first.release()
    controller.reserve(BudgetKind.TOOL_CALLS, 1, agent_id="a1").consume()

    assert controller.usage()[BudgetKind.TOOL_CALLS] == 1
    assert controller.usage(agent_id="a1")[BudgetKind.TOOL_CALLS] == 1


def test_root_exhaustion_cancels_root_but_child_exhaustion_does_not(tmp_path: Path) -> None:
    from harness.budget import BudgetController, BudgetExhausted, BudgetKind

    store = _store(tmp_path)
    root_token = CancellationToken()
    child_token = CancellationToken(parent=root_token)
    controller = BudgetController(store.metadata, "root", cancellation=child_token)
    controller.configure({BudgetKind.TOOL_CALLS: 2})
    controller.configure({BudgetKind.TOOL_CALLS: 1}, agent_id="a1")
    controller.reserve(BudgetKind.TOOL_CALLS, 1, agent_id="a1").consume()
    with pytest.raises(BudgetExhausted):
        controller.reserve(BudgetKind.TOOL_CALLS, 1, agent_id="a1")
    assert root_token.cancelled is False

    controller.reserve(BudgetKind.TOOL_CALLS, 1, agent_id="a2").consume()
    with pytest.raises(BudgetExhausted):
        controller.reserve(BudgetKind.TOOL_CALLS, 1, agent_id="a2")
    assert root_token.cancelled is True


@pytest.mark.asyncio
async def test_tool_pipeline_uses_budget_and_traces_terminal_result(tmp_path: Path) -> None:
    from harness import PermissionMode, SessionHarnessFactory, TerminationReason
    from harness.budget import BudgetKind
    from state_core import SessionRuntimeFactory, TraceSpanStatus

    store = _store(tmp_path)
    file_path = tmp_path / "sample.txt"
    file_path.write_text("ok", encoding="utf-8")
    harness = SessionHarnessFactory(
        SessionRuntimeFactory(store),
        workspace_root=tmp_path,
        permission_mode=PermissionMode.BYPASS,
    ).create("root")
    harness.budget.configure({BudgetKind.TOOL_CALLS: 1})

    first = await harness.tool_runtime.execute(
        "read_file", {"file_path": "sample.txt"}, harness.runtime_context
    )
    second = await harness.tool_runtime.execute(
        "read_file", {"file_path": "sample.txt"}, harness.runtime_context
    )

    assert first.result.success is True
    assert second.termination_reason is TerminationReason.BUDGET_EXHAUSTED
    assert harness.budget.usage()[BudgetKind.TOOL_CALLS] == 1
    statuses = [span.status for span in store.traces.list("root")]
    assert statuses == [TraceSpanStatus.COMPLETED, TraceSpanStatus.FAILED]
