from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from models import Base
from state_core import (
    EventType,
    InvalidTaskDependency,
    NewTask,
    PendingEventBatch,
    PendingSessionEvent,
    RevisionConflict,
    SessionSnapshot,
    SessionState,
    SQLAlchemyStateStore,
    TaskMutation,
    TaskStatus,
)


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'state-core.db'}",
        connect_args={"timeout": 10},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def store(session_factory: sessionmaker[Session]) -> SQLAlchemyStateStore:
    return SQLAlchemyStateStore(session_factory)


def pending_event(
    sequence: int,
    session_id: str,
    *,
    parent_sequence: int | None = None,
    parent_event_id: int | None = None,
) -> PendingSessionEvent:
    return PendingSessionEvent(
        sequence=sequence,
        session_id=session_id,
        event_type=EventType.TOOL_RESULT,
        payload={"sequence": sequence},
        parent_sequence=parent_sequence,
        parent_event_id=parent_event_id,
    )


def test_session_create_and_load_round_trip(store: SQLAlchemyStateStore) -> None:
    state = SessionState.new("session-1")
    state.permission_mode = "acceptEdits"

    created = store.states.create_session(state)

    assert created == state
    assert store.states.load_session("session-1") == state
    assert store.states.load_session("missing") is None


def test_commit_assigns_event_ids_and_resolves_same_batch_parent(
    store: SQLAlchemyStateStore,
) -> None:
    state = SessionState.new("session-events")
    store.states.create_session(state)
    batch = PendingEventBatch(
        state.session_id,
        [
            pending_event(0, state.session_id),
            pending_event(1, state.session_id, parent_sequence=0),
        ],
    )

    result = store.states.commit(state, batch, expected_revision=0)

    assert result.state.revision == 1
    assert [event.id for event in result.events] == [1, 2]
    assert result.events[1].parent_event_id == result.events[0].id
    assert result.state.last_event_id == 2
    assert store.states.load_session(state.session_id) == result.state
    assert store.states.list_events(state.session_id) == result.events


def test_commit_accepts_existing_parent_from_same_session(store: SQLAlchemyStateStore) -> None:
    state = SessionState.new("session-parent")
    store.states.create_session(state)
    first = store.states.commit(
        state,
        PendingEventBatch(state.session_id, [pending_event(0, state.session_id)]),
        expected_revision=0,
    )
    second_batch = PendingEventBatch(
        state.session_id,
        [pending_event(0, state.session_id, parent_event_id=first.events[0].id)],
    )

    second = store.states.commit(first.state, second_batch, expected_revision=1)

    assert second.events[0].parent_event_id == first.events[0].id
    assert store.states.list_events(state.session_id, after_id=first.events[0].id) == second.events


def test_commit_rejects_cross_session_parent_without_writes(store: SQLAlchemyStateStore) -> None:
    first_state = SessionState.new("session-a")
    second_state = SessionState.new("session-b")
    store.states.create_session(first_state)
    store.states.create_session(second_state)
    first_commit = store.states.commit(
        first_state,
        PendingEventBatch(first_state.session_id, [pending_event(0, first_state.session_id)]),
        expected_revision=0,
    )
    batch = PendingEventBatch(
        second_state.session_id,
        [
            pending_event(
                0,
                second_state.session_id,
                parent_event_id=first_commit.events[0].id,
            )
        ],
    )

    with pytest.raises(ValueError, match="same session"):
        store.states.commit(second_state, batch, expected_revision=0)

    assert store.states.load_session(second_state.session_id) == second_state
    assert store.states.list_events(second_state.session_id) == []


def test_revision_conflict_rolls_back_events(store: SQLAlchemyStateStore) -> None:
    state = SessionState.new("session-conflict")
    store.states.create_session(state)
    batch = PendingEventBatch(state.session_id, [pending_event(0, state.session_id)])

    with pytest.raises(RevisionConflict) as error:
        store.states.commit(state, batch, expected_revision=7)

    assert error.value.expected_revision == 7
    assert error.value.actual_revision == 0
    assert store.states.load_session(state.session_id) == state
    assert store.states.list_events(state.session_id) == []


def test_event_insert_failure_does_not_report_or_persist_state(
    store: SQLAlchemyStateStore,
    session_factory: sessionmaker[Session],
) -> None:
    state = SessionState.new("session-failure")
    store.states.create_session(state)
    with session_factory() as db, db.begin():
        db.execute(
            text(
                "CREATE TRIGGER reject_runtime_event BEFORE INSERT ON runtime_events "
                "BEGIN SELECT RAISE(FAIL, 'event rejected'); END"
            )
        )
    batch = PendingEventBatch(state.session_id, [pending_event(0, state.session_id)])

    with pytest.raises(Exception, match="event rejected"):
        store.states.commit(state, batch, expected_revision=0)

    assert store.states.load_session(state.session_id) == state
    assert store.states.list_events(state.session_id) == []


def test_snapshot_round_trip_returns_latest_cursor(store: SQLAlchemyStateStore) -> None:
    state = SessionState.new("session-snapshot")
    store.states.create_session(state)
    committed = store.states.commit(
        state,
        PendingEventBatch(state.session_id, [pending_event(0, state.session_id)]),
        expected_revision=0,
    )
    snapshot = SessionSnapshot(
        session_id=state.session_id,
        last_event_id=committed.state.last_event_id,
        state=committed.state,
    )

    store.states.save_snapshot(snapshot)

    assert store.states.latest_snapshot(state.session_id) == snapshot
    assert store.states.latest_snapshot("missing") is None


def new_task(subject: str, **metadata: object) -> NewTask:
    return NewTask(subject=subject, description=f"Description for {subject}", metadata=metadata)


def test_task_ids_never_reuse_and_list_has_stable_numeric_order(
    store: SQLAlchemyStateStore,
) -> None:
    created = [store.tasks.create("tasks", new_task(f"Task {index}")) for index in range(12)]
    assert [task.id for task in created] == [str(index) for index in range(1, 13)]
    assert store.tasks.delete("tasks", "5") is True
    assert store.tasks.delete("tasks", "5") is False

    replacement = store.tasks.create("tasks", new_task("Replacement"))

    assert replacement.id == "13"
    assert [task.id for task in store.tasks.list("tasks")] == [
        "1",
        "2",
        "3",
        "4",
        "6",
        "7",
        "8",
        "9",
        "10",
        "11",
        "12",
        "13",
    ]
    assert store.tasks.get("tasks", "13") == replacement
    assert store.tasks.get("other-list", "13") is None


def test_concurrent_task_creation_allocates_unique_monotonic_ids(
    session_factory: sessionmaker[Session],
) -> None:
    stores = [SQLAlchemyStateStore(session_factory) for _ in range(8)]
    barrier = Barrier(len(stores))

    def create(index: int) -> str:
        barrier.wait()
        return stores[index].tasks.create("concurrent", new_task(f"Task {index}")).id

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        ids = list(pool.map(create, range(len(stores))))

    assert sorted(ids, key=int) == [str(index) for index in range(1, len(stores) + 1)]
    assert [task.id for task in stores[0].tasks.list("concurrent")] == sorted(ids, key=int)


def test_task_id_reservation_is_not_reused_after_insert_retry(
    session_factory: sessionmaker[Session],
) -> None:
    store = SQLAlchemyStateStore(session_factory)
    with session_factory() as db, db.begin():
        db.execute(
            text(
                "CREATE TRIGGER reject_runtime_task BEFORE INSERT ON runtime_tasks "
                "BEGIN SELECT RAISE(FAIL, 'task rejected'); END"
            )
        )

    with pytest.raises(Exception, match="task rejected"):
        store.tasks.create("retry", new_task("Rejected"))

    with session_factory() as db, db.begin():
        db.execute(text("DROP TRIGGER reject_runtime_task"))
    retried = store.tasks.create("retry", new_task("Retried"))

    assert retried.id == "2"


def test_task_update_preserves_exact_reciprocal_edges_and_ignores_duplicates(
    store: SQLAlchemyStateStore,
) -> None:
    first = store.tasks.create("tasks", new_task("First"))
    second = store.tasks.create("tasks", new_task("Second"))
    third = store.tasks.create("tasks", new_task("Third"))

    updated = store.tasks.update(
        "tasks",
        first.id,
        TaskMutation(
            add_blocks=[second.id, second.id],
            add_blocked_by=[third.id, third.id],
        ),
    )

    assert updated is not None
    assert updated.blocks == [second.id]
    assert updated.blocked_by == [third.id]
    assert store.tasks.get("tasks", second.id).blocked_by == [first.id]  # type: ignore[union-attr]
    assert store.tasks.get("tasks", third.id).blocks == [first.id]  # type: ignore[union-attr]

    removed = store.tasks.update(
        "tasks",
        first.id,
        TaskMutation(remove_blocks=[second.id], remove_blocked_by=[third.id]),
    )

    assert removed is not None
    assert removed.blocks == []
    assert removed.blocked_by == []
    assert store.tasks.get("tasks", second.id).blocked_by == []  # type: ignore[union-attr]
    assert store.tasks.get("tasks", third.id).blocks == []  # type: ignore[union-attr]


def test_concurrent_dependency_updates_preserve_both_reciprocal_edges(
    session_factory: sessionmaker[Session],
) -> None:
    setup = SQLAlchemyStateStore(session_factory)
    first = setup.tasks.create("concurrent-deps", new_task("First"))
    second = setup.tasks.create("concurrent-deps", new_task("Second"))
    third = setup.tasks.create("concurrent-deps", new_task("Third"))
    barrier = Barrier(2)

    def add_dependency(task_id: str, dependency_id: str) -> None:
        contender = SQLAlchemyStateStore(session_factory)
        barrier.wait()
        contender.tasks.update("concurrent-deps", task_id, TaskMutation(add_blocks=[dependency_id]))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(add_dependency, first.id, second.id),
            pool.submit(add_dependency, first.id, third.id),
        ]
        for future in futures:
            future.result()

    final_first = setup.tasks.get("concurrent-deps", first.id)
    final_second = setup.tasks.get("concurrent-deps", second.id)
    final_third = setup.tasks.get("concurrent-deps", third.id)
    assert final_first is not None
    assert final_second is not None
    assert final_third is not None
    assert set(final_first.blocks) == {second.id, third.id}
    assert final_second.blocked_by == [first.id]
    assert final_third.blocked_by == [first.id]


@pytest.mark.parametrize("dependency_kind", ["self", "missing"])
def test_task_update_rejects_invalid_dependencies_without_partial_changes(
    store: SQLAlchemyStateStore,
    dependency_kind: str,
) -> None:
    task = store.tasks.create("tasks", new_task("Task"))
    dependency_id = task.id if dependency_kind == "self" else "999"

    with pytest.raises(InvalidTaskDependency):
        store.tasks.update("tasks", task.id, TaskMutation(add_blocks=[dependency_id]))

    assert store.tasks.get("tasks", task.id) == task


def test_task_metadata_merges_and_none_deletes_keys(store: SQLAlchemyStateStore) -> None:
    task = store.tasks.create("tasks", new_task("Task", keep="yes", remove="old"))

    updated = store.tasks.update(
        "tasks",
        task.id,
        TaskMutation(metadata={"remove": None, "added": {"nested": True}}),
    )

    assert updated is not None
    assert updated.metadata == {"keep": "yes", "added": {"nested": True}}


def test_delete_removes_all_reverse_dependency_edges(store: SQLAlchemyStateStore) -> None:
    first = store.tasks.create("tasks", new_task("First"))
    middle = store.tasks.create("tasks", new_task("Middle"))
    last = store.tasks.create("tasks", new_task("Last"))
    store.tasks.update("tasks", middle.id, TaskMutation(add_blocked_by=[first.id]))
    store.tasks.update("tasks", middle.id, TaskMutation(add_blocks=[last.id]))

    assert store.tasks.delete("tasks", middle.id) is True

    assert store.tasks.get("tasks", first.id).blocks == []  # type: ignore[union-attr]
    assert store.tasks.get("tasks", last.id).blocked_by == []  # type: ignore[union-attr]


def test_claim_has_exactly_one_winner_across_independent_sessions(
    store: SQLAlchemyStateStore,
    session_factory: sessionmaker[Session],
) -> None:
    task = store.tasks.create("tasks", new_task("Claim me"))
    barrier = Barrier(2)

    def claim(owner: str):
        contender = SQLAlchemyStateStore(session_factory)
        barrier.wait()
        return contender.tasks.claim("tasks", task.id, owner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["agent-a", "agent-b"]))

    winners = [result for result in results if result.success]
    losers = [result for result in results if not result.success]
    assert len(winners) == 1
    assert len(losers) == 1
    assert winners[0].task is not None
    assert winners[0].task.owner in {"agent-a", "agent-b"}
    assert winners[0].task.status is TaskStatus.IN_PROGRESS
    assert losers[0].current_owner == winners[0].task.owner
    assert losers[0].task is not None
    assert losers[0].task.status is TaskStatus.IN_PROGRESS


def test_claim_rejects_blocked_task(store: SQLAlchemyStateStore) -> None:
    blocker = store.tasks.create("tasks", new_task("Blocker"))
    blocked = store.tasks.create("tasks", new_task("Blocked"))
    store.tasks.update("tasks", blocked.id, TaskMutation(add_blocked_by=[blocker.id]))

    result = store.tasks.claim("tasks", blocked.id, "agent-a")

    assert result.success is False
    assert result.reason == "blocked"
    assert result.current_owner is None
    assert result.task is not None
    assert result.task.status is TaskStatus.PENDING
