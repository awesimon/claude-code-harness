from __future__ import annotations

# ruff: noqa: E501
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Event, local
from typing import Callable

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import state_core.runtime_records as runtime_records
from models import Base
from state_core import (
    AgentRecord,
    AgentStatus,
    AgentTerminationReason,
    InvalidAgentParent,
    InvalidAgentTransition,
    InvalidTraceSpanTransition,
    RuntimeRecordRevisionConflict,
    SQLAlchemyStateStore,
    TraceSpanRecord,
    TraceSpanStatus,
    WorktreeRecord,
    WorktreeStatus,
    sanitize_runtime_error,
)


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime-records.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def store(session_factory: sessionmaker[Session]) -> SQLAlchemyStateStore:
    return SQLAlchemyStateStore(session_factory)


def agent(agent_id: str, **overrides: object) -> AgentRecord:
    values: dict[str, object] = {
        "agent_id": agent_id,
        "root_session_id": "root-1",
        "agent_type": "worker",
        "prompt": "Do the work",
        "description": "A durable worker",
        "is_background": False,
        "effective_cwd": "/repo",
        "definition_snapshot": {"model": "test", "nested": {"version": 1}},
    }
    values.update(overrides)
    return AgentRecord(**values)  # type: ignore[arg-type]


def test_agent_lifecycle_persists_across_new_store_and_json_is_detached(
    store: SQLAlchemyStateStore, session_factory: sessionmaker[Session]
) -> None:
    definition = {"model": "test", "nested": {"version": 1}}
    created = store.agents.create(agent("agent-1", definition_snapshot=definition))
    definition["nested"]["version"] = 99
    assert created.definition_snapshot["nested"]["version"] == 1

    running = store.agents.transition(
        created.agent_id, AgentStatus.RUNNING, created.revision
    )
    completed = store.agents.transition(
        running.agent_id,
        AgentStatus.COMPLETED,
        running.revision,
        termination_reason=AgentTerminationReason.COMPLETED,
        output={"result": ["ok"]},
        usage={"total_tokens": 3},
    )
    completed.output["result"].append("mutated")

    restarted_store = SQLAlchemyStateStore(session_factory)
    persisted = restarted_store.agents.get("agent-1")
    assert persisted is not None
    assert persisted.status is AgentStatus.COMPLETED
    assert persisted.termination_reason is AgentTerminationReason.COMPLETED
    assert persisted.output == {"result": ["ok"]}
    assert persisted.usage == {"total_tokens": 3}
    assert persisted.revision == 2


def test_terminal_agent_cannot_restart_invalid_transitions_and_stale_revisions_rejected(
    store: SQLAlchemyStateStore,
) -> None:
    created = store.agents.create(agent("agent-1"))
    with pytest.raises(InvalidAgentTransition):
        store.agents.transition(created.agent_id, AgentStatus.COMPLETED, created.revision)

    running = store.agents.transition(created.agent_id, AgentStatus.RUNNING, created.revision)
    completed = store.agents.transition(
        running.agent_id, AgentStatus.COMPLETED, running.revision
    )
    with pytest.raises(InvalidAgentTransition):
        store.agents.transition(completed.agent_id, AgentStatus.RUNNING, completed.revision)
    with pytest.raises(RuntimeRecordRevisionConflict):
        store.agents.transition(completed.agent_id, AgentStatus.FAILED, running.revision)


def test_agent_listing_and_reconciliation_only_interrupts_non_live_open_agents(
    store: SQLAlchemyStateStore,
) -> None:
    parent = store.agents.create(agent("parent", is_background=True))
    child = store.agents.create(agent("child", parent_agent_id=parent.agent_id))
    other_root = store.agents.create(agent("other", root_session_id="root-2"))
    running = store.agents.transition(child.agent_id, AgentStatus.RUNNING, child.revision)

    assert [item.agent_id for item in store.agents.list("root-1", is_background=True)] == ["parent"]
    assert [item.agent_id for item in store.agents.list("root-1", parent_agent_id="parent")] == [
        "child"
    ]
    assert [item.agent_id for item in store.agents.list("root-1", status=AgentStatus.RUNNING)] == [
        "child"
    ]

    reconciled = store.agents.reconcile("root-1", {running.agent_id})
    assert [item.agent_id for item in reconciled] == ["parent"]
    assert reconciled[0].status is AgentStatus.INTERRUPTED
    assert reconciled[0].termination_reason is AgentTerminationReason.INTERRUPTED
    assert store.agents.get(running.agent_id).status is AgentStatus.RUNNING  # type: ignore[union-attr]
    assert store.agents.get(other_root.agent_id).status is AgentStatus.PENDING  # type: ignore[union-attr]


def test_agent_catalog_lists_all_roots_without_private_storage_access(
    store: SQLAlchemyStateStore,
) -> None:
    store.agents.create(agent("agent-b", root_session_id="root-2"))
    store.agents.create(agent("agent-a", root_session_id="root-1"))

    assert [item.agent_id for item in store.agents.list_all()] == [
        "agent-a",
        "agent-b",
    ]


def test_metadata_put_get_uses_revision_checks_and_detaches_json(store: SQLAlchemyStateStore) -> None:
    value = {"config": {"attempt": 1}}
    created = store.metadata.put("root-1", "harness", value)
    value["config"]["attempt"] = 2
    assert created.snapshot == {"config": {"attempt": 1}}

    loaded = store.metadata.get("root-1", "harness")
    assert loaded == created
    assert loaded is not None
    loaded.snapshot["config"]["attempt"] = 3
    assert store.metadata.get("root-1", "harness").snapshot == {"config": {"attempt": 1}}  # type: ignore[union-attr]

    updated = store.metadata.put("root-1", "harness", {"config": {"attempt": 4}}, 0)
    assert updated.revision == 1
    with pytest.raises(RuntimeRecordRevisionConflict):
        store.metadata.put("root-1", "harness", {"stale": True}, 0)


def test_trace_start_finish_and_interrupt_open_spans(store: SQLAlchemyStateStore) -> None:
    root = store.traces.start(
        TraceSpanRecord(span_id="span-root", root_session_id="root-1", kind="agent", name="root")
    )
    child = store.traces.start(
        TraceSpanRecord(
            span_id="span-child",
            root_session_id="root-1",
            agent_id="agent-1",
            parent_span_id=root.span_id,
            kind="tool",
            name="command",
        )
    )
    finished = store.traces.finish(
        child.span_id, TraceSpanStatus.COMPLETED, child.revision, usage={"tokens": 5}
    )
    interrupted = store.traces.interrupt_open("root-1")

    assert finished.status is TraceSpanStatus.COMPLETED
    assert finished.duration_ms == int(
        (finished.finished_at - finished.started_at).total_seconds() * 1000
    )
    assert [span.span_id for span in interrupted] == [root.span_id]
    assert interrupted[0].status is TraceSpanStatus.INTERRUPTED
    assert interrupted[0].duration_ms == int(
        (interrupted[0].finished_at - interrupted[0].started_at).total_seconds() * 1000
    )
    assert [span.span_id for span in store.traces.list("root-1")] == ["span-root", "span-child"]


def test_trace_errors_are_sanitized_before_persistence(store: SQLAlchemyStateStore) -> None:
    started = store.traces.start(
        TraceSpanRecord(span_id="span-error", root_session_id="root-1", kind="tool", name="request")
    )

    finished = store.traces.finish(
        started.span_id,
        TraceSpanStatus.FAILED,
        started.revision,
        error={
            "Authorization": "Bearer secret",
            "X-API-Key": "prefixed secret",
            "context": {
                "api_key": "nested secret",
                "apiKey": "camel secret",
                "cookies": "cookie secret",
                "safe": "kept",
                "clientSecret": "oauth secret",
                "secret_access_key": "cloud secret",
                "privateKey": "pem secret",
                "signing_key": "signing secret",
                "bearer_token": "bearer secret",
                "id_token": "id secret",
                "session_token": "session secret",
                "credentials": "credential secret",
                "token_count": 7,
                "secretariat": "safe word",
                "database_password": "db secret",
                "authToken": "auth secret",
                "oauth_client_secret": "oauth secret",
                "input_tokens": 3,
            },
            "requestHeaders": {"Authorization": "raw header secret"},
        },
    )
    persisted = store.traces.get(started.span_id)

    expected = {
        "Authorization": "[REDACTED]",
        "X-API-Key": "[REDACTED]",
        "context": {
            "api_key": "[REDACTED]",
            "apiKey": "[REDACTED]",
            "cookies": "[REDACTED]",
            "safe": "kept",
            "clientSecret": "[REDACTED]",
            "secret_access_key": "[REDACTED]",
            "privateKey": "[REDACTED]",
            "signing_key": "[REDACTED]",
            "bearer_token": "[REDACTED]",
            "id_token": "[REDACTED]",
            "session_token": "[REDACTED]",
            "credentials": "[REDACTED]",
            "token_count": 7,
            "secretariat": "safe word",
            "database_password": "[REDACTED]",
            "authToken": "[REDACTED]",
            "oauth_client_secret": "[REDACTED]",
            "input_tokens": 3,
        },
        "requestHeaders": "[REDACTED]",
    }
    assert finished.error == expected
    assert persisted is not None
    assert persisted.error == expected


def test_runtime_error_sanitizer_redacts_embedded_credentials_and_bounds_strings() -> None:
    sanitized = sanitize_runtime_error(
        {
            "type": "RuntimeError",
            "message": (
                "Bearer standalone-secret; Authorization: Bearer header-secret; "
                "api_key=key-secret&password=password-secret; "
                'Authorization: "Bearer quoted-header-secret"; '
                'api_key="quoted key secret"; '
                'password="escaped-\\"quote-secret"; '
                'token="prefix scan-truncated-secret ' + "x" * 5000 + '"'
            ),
            "details": "x" * 5000,
            "nested": {"safe": "kept", "access_token": "field-secret"},
        }
    )

    assert sanitized is not None
    serialized = json.dumps(sanitized)
    for secret in (
        "standalone-secret",
        "header-secret",
        "key-secret",
        "password-secret",
        "quoted-header-secret",
        "quoted key secret",
        "quote-secret",
        "scan-truncated-secret",
        "field-secret",
    ):
        assert secret not in serialized
    assert sanitized["nested"]["safe"] == "kept"
    assert len(sanitized["details"]) <= 2000


def test_runtime_error_sanitizer_redacts_json_and_escaped_json_credentials() -> None:
    sanitized = sanitize_runtime_error(
        {
            "message": (
                '{"api_key":"json-key-secret","safe":"kept"}; '
                r'{\"access_token\":\"escaped-json-secret\"}'
            )
        }
    )

    serialized = json.dumps(sanitized)
    assert "json-key-secret" not in serialized
    assert "escaped-json-secret" not in serialized


def test_runtime_error_sanitizer_redacts_multiply_escaped_json_credentials() -> None:
    sanitized = sanitize_runtime_error(
        {
            "message": (
                r'{\\\"api_key\\\":\\\"double-escaped-secret\\\"}; '
                r'{\\\\\\\"password\\\\\\\":\\\\\\\"quad-escaped-secret\\\\\\\"}'
            )
        }
    )

    serialized = json.dumps(sanitized)
    assert "double-escaped-secret" not in serialized
    assert "quad-escaped-secret" not in serialized


@pytest.mark.parametrize(
    "value",
    [
        "plain error",
        float("nan"),
        {"value": float("inf")},
        {"value": object()},
        {1: "non-string key"},
    ],
)
def test_runtime_error_sanitizer_is_total_and_json_safe(value: object) -> None:
    sanitized = sanitize_runtime_error(value)  # type: ignore[arg-type]

    assert isinstance(sanitized, dict)
    assert len(json.dumps(sanitized, allow_nan=False)) <= 50000


def test_runtime_error_sanitizer_stops_before_wide_invalid_key_iteration() -> None:
    class WideInvalidKeyMapping(dict[object, object]):
        def items(self):
            for index in range(65):
                yield index, "value"
            raise AssertionError("sanitizer traversed beyond its item budget")

    sanitized = sanitize_runtime_error(WideInvalidKeyMapping())

    assert sanitized is not None
    assert sanitized.get("__truncated__") == "[TRUNCATED]"
    assert "[UNSANITIZABLE]" not in json.dumps(sanitized)


def test_runtime_error_sanitizer_bounds_nested_invalid_key_markers() -> None:
    value: dict[object, object] = {"message": "bottom"}
    for _ in range(16):
        value = {
            **{index: "invalid" for index in range(63)},
            "nested": value,
        }

    sanitized = sanitize_runtime_error(value)

    assert sanitized is not None
    assert len(json.dumps(sanitized)) <= 50000


def test_runtime_error_sanitizer_bounds_depth_width_and_total_size() -> None:
    deep: dict[str, object] = {"message": "bottom"}
    for _ in range(2000):
        deep = {"nested": deep}
    value = {
        "deep": deep,
        "wide": list(range(1000)),
        "large": {f"field-{index}": "x" * 5000 for index in range(1000)},
    }

    sanitized = sanitize_runtime_error(value)

    assert sanitized is not None
    serialized = json.dumps(sanitized)
    assert "[TRUNCATED]" in serialized
    assert len(sanitized["wide"]) <= 65
    assert len(serialized) <= 50000


def test_runtime_error_sanitizer_bounds_json_escaped_text_and_large_integers() -> None:
    escaped = sanitize_runtime_error(
        {"values": ["\x00" * 5000 for _ in range(64)]}
    )
    large_integer = sanitize_runtime_error({"value": 1 << 200_000})

    assert escaped is not None
    assert large_integer is not None
    assert len(json.dumps(escaped)) <= 50000
    assert large_integer["value"] == "[TRUNCATED]"
    assert len(json.dumps(large_integer)) <= 50000


def test_runtime_error_sanitizer_fails_closed_for_boundary_quote_escapes() -> None:
    prefix = 'token="prefix dangling-backslash-secret '
    dangling_at_scan_boundary = (
        prefix
        + "x" * (4095 - len(prefix))
        + "\\"
        + 'closing-text"'
    )

    sanitized = sanitize_runtime_error(
        {
            "boundary": dangling_at_scan_boundary,
            "newline": 'password="prefix escaped-newline-secret\\\nremainder"',
        }
    )

    assert sanitized is not None
    serialized = json.dumps(sanitized)
    assert "dangling-backslash-secret" not in serialized
    assert "escaped-newline-secret" not in serialized


def test_runtime_error_sanitizer_bounds_inputs_before_regex_and_key_scans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanned_lengths: list[int] = []
    original_sensitive_key = runtime_records._is_sensitive_runtime_error_key

    class PatternSpy:
        def __init__(self, pattern: re.Pattern[str]) -> None:
            self._pattern = pattern

        def sub(self, replacement: str, value: str) -> str:
            scanned_lengths.append(len(value))
            return self._pattern.sub(replacement, value)

    def sensitive_key_spy(key: str) -> bool:
        scanned_lengths.append(len(key))
        return original_sensitive_key(key)

    monkeypatch.setattr(
        runtime_records,
        "_AUTHORIZATION_VALUE",
        PatternSpy(runtime_records._AUTHORIZATION_VALUE),
    )
    monkeypatch.setattr(
        runtime_records,
        "_CREDENTIAL_ASSIGNMENT",
        PatternSpy(runtime_records._CREDENTIAL_ASSIGNMENT),
    )
    monkeypatch.setattr(
        runtime_records,
        "_BEARER_VALUE",
        PatternSpy(runtime_records._BEARER_VALUE),
    )
    monkeypatch.setattr(
        runtime_records, "_is_sensitive_runtime_error_key", sensitive_key_spy
    )

    sanitized = sanitize_runtime_error(
        {"x" * 100_000: "y" * 100_000, "message": "z" * 100_000}
    )

    assert sanitized is not None
    assert scanned_lengths
    assert max(scanned_lengths) <= 4096


def test_agent_error_is_sanitized_before_physical_persistence(
    store: SQLAlchemyStateStore, session_factory: sessionmaker[Session]
) -> None:
    created = store.agents.create(agent("agent-error"))
    running = store.agents.transition(
        created.agent_id, AgentStatus.RUNNING, created.revision
    )

    failed = store.agents.transition(
        running.agent_id,
        AgentStatus.FAILED,
        running.revision,
        error={
            "type": "RuntimeError",
            "message": "Authorization: Bearer database-secret",
        },
    )

    assert "database-secret" not in str(failed.error)
    with session_factory() as db:
        physical = db.execute(
            text(
                "SELECT error_json FROM runtime_agents WHERE agent_id = 'agent-error'"
            )
        ).scalar_one()
    assert "database-secret" not in str(physical)


def test_trace_rejects_terminal_lifecycle_transition(store: SQLAlchemyStateStore) -> None:
    started = store.traces.start(
        TraceSpanRecord(span_id="span-terminal", root_session_id="root-1", kind="tool", name="request")
    )
    finished = store.traces.finish(started.span_id, TraceSpanStatus.COMPLETED, started.revision)

    with pytest.raises(InvalidTraceSpanTransition) as error:
        store.traces.finish(finished.span_id, TraceSpanStatus.FAILED, finished.revision)

    assert error.value.current is TraceSpanStatus.COMPLETED
    assert error.value.target is TraceSpanStatus.FAILED


def test_runtime_record_status_invariants_are_enforced() -> None:
    with pytest.raises(ValueError, match="termination_reason"):
        agent("completed", status=AgentStatus.COMPLETED)
    with pytest.raises(ValueError, match="termination_reason"):
        agent(
            "wrong-reason",
            status=AgentStatus.FAILED,
            termination_reason=AgentTerminationReason.COMPLETED,
        )
    with pytest.raises(ValueError, match="terminal fields"):
        agent("pending-output", output={"unexpected": True})
    with pytest.raises(ValueError, match="finished_at"):
        TraceSpanRecord(
            span_id="unfinished-terminal",
            root_session_id="root-1",
            kind="tool",
            name="request",
            status=TraceSpanStatus.COMPLETED,
        )
    with pytest.raises(ValueError, match="removed_at"):
        WorktreeRecord(
            worktree_id="not-removed",
            root_session_id="root-1",
            repository_root="/repo",
            canonical_path="/repo/wt",
            branch="branch",
            base_commit="abc",
            removed_at=TraceSpanRecord(
                span_id="time", root_session_id="root-1", kind="tool", name="time"
            ).started_at,
        )


def test_metadata_create_race_returns_domain_conflict(
    session_factory: sessionmaker[Session],
) -> None:
    stores = [SQLAlchemyStateStore(session_factory) for _ in range(2)]
    barrier = Barrier(2)

    def put(index: int) -> object:
        barrier.wait()
        try:
            return stores[index].metadata.put("root-race", "namespace", {"writer": index})
        except RuntimeRecordRevisionConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(put, range(2)))

    assert sum(not isinstance(outcome, RuntimeRecordRevisionConflict) for outcome in outcomes) == 1
    conflicts = [outcome for outcome in outcomes if isinstance(outcome, RuntimeRecordRevisionConflict)]
    assert len(conflicts) == 1
    assert conflicts[0].actual_revision == 0


def test_parent_guard_serializes_against_concurrent_sqlite_transition(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'parent-guard-race.db'}?timeout=0.2"
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    transition_repository = SQLAlchemyStateStore(session_factory).agents
    guard_repository = SQLAlchemyStateStore(session_factory).agents
    parent = transition_repository.create(agent("parent-race"))
    parent = transition_repository.transition(
        parent.agent_id, AgentStatus.RUNNING, parent.revision
    )
    child = agent("child-race", parent_agent_id=parent.agent_id)
    transition_entered = Event()
    release_transition = Event()
    guard_started = Event()

    def hold_transition() -> None:
        transition_entered.set()
        assert release_transition.wait(timeout=1)

    def create_guarded() -> AgentRecord:
        guard_started.set()
        return guard_repository.create_with_parent_guard(child)

    transition_repository._before_compare_and_swap = hold_transition
    with ThreadPoolExecutor(max_workers=2) as pool:
        transition_future = pool.submit(
            transition_repository.transition,
            parent.agent_id,
            AgentStatus.COMPLETED,
            parent.revision,
        )
        assert transition_entered.wait(timeout=1)
        guard_future = pool.submit(create_guarded)
        assert guard_started.wait(timeout=1)
        time.sleep(0.05)
        release_transition.set()

        transitioned = transition_future.result(timeout=1)
        assert transitioned.status is AgentStatus.COMPLETED
        with pytest.raises(InvalidAgentParent):
            guard_future.result(timeout=1)

    assert guard_repository.get(child.agent_id) is None


def test_trace_repository_adds_duration_column_to_existing_sqlite_table(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pre-duration.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE runtime_trace_spans ("
                "span_id VARCHAR PRIMARY KEY, root_session_id VARCHAR NOT NULL, "
                "agent_id VARCHAR, parent_span_id VARCHAR, kind VARCHAR NOT NULL, "
                "name VARCHAR NOT NULL, status VARCHAR NOT NULL, revision INTEGER NOT NULL, "
                "started_at DATETIME NOT NULL, finished_at DATETIME, usage JSON NOT NULL, "
                "error_json JSON, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO runtime_trace_spans "
                "(span_id, root_session_id, agent_id, parent_span_id, kind, name, status, revision, "
                "started_at, finished_at, usage, error_json, created_at, updated_at) "
                "VALUES (:span_id, :root_session_id, :agent_id, :parent_span_id, :kind, :name, "
                ":status, :revision, :started_at, :finished_at, :usage, :error_json, :created_at, "
                ":updated_at)"
            ),
            {
                "span_id": "legacy-completed",
                "root_session_id": "root-1",
                "agent_id": None,
                "parent_span_id": None,
                "kind": "tool",
                "name": "legacy request",
                "status": "completed",
                "revision": 1,
                "started_at": "2026-07-24 00:00:00.100000",
                "finished_at": "2026-07-24 00:00:02.600000",
                "usage": "{}",
                "error_json": json.dumps(
                    {
                        "Authorization": "Bearer legacy-secret",
                        "X-API-Key": "legacy-api-key",
                        "context": {"apiKey": "legacy-camel-key"},
                        "responseHeaders": {"Set-Cookie": "legacy-cookie"},
                    }
                ),
                "created_at": "2026-07-24 00:00:00.100000",
                "updated_at": "2026-07-24 00:00:02.600000",
            },
        )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    store = SQLAlchemyStateStore(session_factory)
    with engine.connect() as connection:
        migrated = connection.execute(
            text("SELECT duration_ms, error_json FROM runtime_trace_spans WHERE span_id = 'legacy-completed'")
        ).one()
    assert migrated.duration_ms == 2500
    physical_error = str(migrated.error_json)
    assert "legacy-secret" not in physical_error
    assert "legacy-api-key" not in physical_error
    assert "legacy-camel-key" not in physical_error
    assert "legacy-cookie" not in physical_error
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version FROM runtime_schema_migrations WHERE name = 'runtime_trace_spans'")
        ).scalar_one() == 2

    started = store.traces.start(
        TraceSpanRecord(span_id="legacy-span", root_session_id="root-1", kind="tool", name="request")
    )
    finished = store.traces.finish(
        started.span_id, TraceSpanStatus.COMPLETED, started.revision
    )

    assert finished.duration_ms is not None


def test_concurrent_legacy_trace_schema_initialization_is_versioned(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-legacy.db'}", connect_args={"timeout": 10}
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE runtime_trace_spans ("
                "span_id VARCHAR PRIMARY KEY, root_session_id VARCHAR NOT NULL, "
                "agent_id VARCHAR, parent_span_id VARCHAR, kind VARCHAR NOT NULL, "
                "name VARCHAR NOT NULL, status VARCHAR NOT NULL, revision INTEGER NOT NULL, "
                "started_at DATETIME NOT NULL, finished_at DATETIME, usage JSON NOT NULL, "
                "error_json JSON, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
            )
        )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    barrier = Barrier(2)

    def initialize() -> SQLAlchemyStateStore:
        barrier.wait()
        return SQLAlchemyStateStore(session_factory)

    with ThreadPoolExecutor(max_workers=2) as pool:
        stores = list(pool.map(lambda _: initialize(), range(2)))

    assert len(stores) == 2
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version FROM runtime_schema_migrations WHERE name = 'runtime_trace_spans'")
        ).scalar_one() == 2
        assert "duration_ms" in {
            row.name for row in connection.execute(text("PRAGMA table_info(runtime_trace_spans)"))
        }


def test_legacy_trace_migration_truncates_fractional_milliseconds(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fractional-legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE runtime_trace_spans ("
                "span_id VARCHAR PRIMARY KEY, root_session_id VARCHAR NOT NULL, "
                "agent_id VARCHAR, parent_span_id VARCHAR, kind VARCHAR NOT NULL, "
                "name VARCHAR NOT NULL, status VARCHAR NOT NULL, revision INTEGER NOT NULL, "
                "started_at DATETIME NOT NULL, finished_at DATETIME, usage JSON NOT NULL, "
                "error_json JSON, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO runtime_trace_spans "
                "(span_id, root_session_id, agent_id, parent_span_id, kind, name, status, revision, "
                "started_at, finished_at, usage, error_json, created_at, updated_at) "
                "VALUES (:span_id, 'root-1', NULL, NULL, 'tool', :span_id, 'completed', 0, "
                "'2026-07-24 00:00:00.000000', :finished_at, '{}', NULL, "
                "'2026-07-24 00:00:00.000000', :finished_at)"
            ),
            [
                {"span_id": "fraction-600us", "finished_at": "2026-07-24 00:00:00.000600"},
                {"span_id": "fraction-1600us", "finished_at": "2026-07-24 00:00:00.001600"},
            ],
        )

    store = SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))
    with engine.connect() as connection:
        raw_durations = dict(
            connection.execute(
                text("SELECT span_id, duration_ms FROM runtime_trace_spans ORDER BY span_id")
            ).all()
        )
    assert raw_durations == {"fraction-1600us": 1, "fraction-600us": 0}
    assert store.traces.get("fraction-600us").duration_ms == 0  # type: ignore[union-attr]
    assert store.traces.get("fraction-1600us").duration_ms == 1  # type: ignore[union-attr]


def test_connection_bound_session_factory_runs_runtime_migration(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'connection-bound.db'}")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        factory = sessionmaker(bind=connection, expire_on_commit=False)
        store = SQLAlchemyStateStore(factory)
        created = store.traces.start(
            TraceSpanRecord(span_id="connection-span", root_session_id="root-1", kind="tool", name="request")
        )
        assert store.traces.get(created.span_id) == created


def test_bare_connection_migration_commits_and_runtime_records_are_durable(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'bare-connection.db'}")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        store = SQLAlchemyStateStore(sessionmaker(bind=connection, expire_on_commit=False))
        assert not connection.in_transaction()
        store.agents.create(agent("durable-agent"))
        store.traces.start(
            TraceSpanRecord(span_id="durable-span", root_session_id="root-1", kind="tool", name="request")
        )

    reopened = SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))
    assert reopened.agents.get("durable-agent") is not None
    assert reopened.traces.get("durable-span") is not None


def test_connection_owned_transaction_survives_migration_and_controls_runtime_writes(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'caller-transaction.db'}")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        transaction = connection.begin()
        store = SQLAlchemyStateStore(sessionmaker(bind=connection, expire_on_commit=False))
        assert connection.in_transaction()
        store.agents.create(agent("rolled-back-agent"))
        transaction.rollback()

    reopened = SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))
    assert reopened.agents.get("rolled-back-agent") is None


def test_v2_migration_rewrites_v1_history_once(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'v1-runtime.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_runtime_agents_root_status_created"))
        connection.execute(
            text(
                "CREATE TABLE runtime_schema_migrations "
                "(name TEXT PRIMARY KEY, version INTEGER NOT NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO runtime_schema_migrations (name, version) VALUES ('runtime_trace_spans', 1)")
        )
        connection.execute(
            text(
                "INSERT INTO runtime_trace_spans "
                "(span_id, root_session_id, agent_id, parent_span_id, kind, name, status, revision, "
                "started_at, finished_at, duration_ms, usage, error_json, created_at, updated_at) "
                "VALUES ('v1-span', 'root-1', NULL, NULL, 'tool', 'request', 'failed', 1, "
                "'2026-07-24 00:00:00.000000', '2026-07-24 00:00:00.000600', 1, '{}', "
                ":error_json, '2026-07-24 00:00:00.000000', '2026-07-24 00:00:00.000600')"
            ),
            {"error_json": json.dumps({"database_password": "legacy secret", "safe": "kept"})},
        )
        connection.execute(text("CREATE TABLE migration_updates (count INTEGER NOT NULL)"))
        connection.execute(text("INSERT INTO migration_updates VALUES (0)"))
        connection.execute(
            text(
                "CREATE TRIGGER count_v2_trace_updates BEFORE UPDATE ON runtime_trace_spans "
                "BEGIN UPDATE migration_updates SET count = count + 1; END"
            )
        )

    store = SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version FROM runtime_schema_migrations WHERE name = 'runtime_trace_spans'")
        ).scalar_one() == 2
        assert connection.execute(
            text("SELECT duration_ms FROM runtime_trace_spans WHERE span_id = 'v1-span'")
        ).scalar_one() == 0
        assert "legacy secret" not in str(
            connection.execute(
                text("SELECT error_json FROM runtime_trace_spans WHERE span_id = 'v1-span'")
            ).scalar_one()
        )
        assert "ix_runtime_agents_root_status_created" in {
            row.name for row in connection.execute(text("PRAGMA index_list(runtime_agents)"))
        }
        assert connection.execute(text("SELECT count FROM migration_updates")).scalar_one() > 0
        connection.execute(text("UPDATE migration_updates SET count = 0"))
        connection.commit()

    assert store.traces.get("v1-span").duration_ms == 0  # type: ignore[union-attr]
    SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count FROM migration_updates")).scalar_one() == 0


def test_concurrent_runtime_cas_losers_report_committed_winner_revision(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLAlchemyStateStore(session_factory)
    created_agent = store.agents.create(agent("race-agent"))
    created_span = store.traces.start(
        TraceSpanRecord(span_id="race-span", root_session_id="root-1", kind="tool", name="request")
    )
    created_worktree = store.worktrees.create(
        WorktreeRecord(
            worktree_id="race-worktree",
            root_session_id="root-1",
            repository_root="/repo",
            canonical_path="/repo/wt",
            branch="branch",
            base_commit="abc",
            status=WorktreeStatus.CREATING,
        )
    )
    thread_state = local()

    agent_winner_locked = Event()
    release_agent_winner = Event()

    def serialize_agent_winner() -> None:
        assert getattr(thread_state, "role", None) == "winner"
        agent_winner_locked.set()
        assert release_agent_winner.wait(timeout=5)

    monkeypatch.setattr(
        store.agents, "_before_compare_and_swap", serialize_agent_winner
    )

    def run_agent(role: str) -> AgentRecord | RuntimeRecordRevisionConflict:
        thread_state.role = role
        try:
            return store.agents.transition(
                created_agent.agent_id, AgentStatus.RUNNING, 0
            )
        except RuntimeRecordRevisionConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        winner_future = pool.submit(run_agent, "winner")
        assert agent_winner_locked.wait(timeout=5)
        loser_future = pool.submit(run_agent, "loser")
        time.sleep(0.05)
        release_agent_winner.set()
        agent_winner = winner_future.result(timeout=5)
        agent_loser = loser_future.result(timeout=5)

    assert not isinstance(agent_winner, RuntimeRecordRevisionConflict)
    assert isinstance(agent_loser, RuntimeRecordRevisionConflict)
    assert agent_loser.expected_revision == 0
    assert agent_loser.actual_revision == 1

    def assert_race(repository: object, winner: Callable[[], object], loser: Callable[[], object]) -> None:
        both_loaded = Barrier(2)
        winner_committed = Event()

        def interleave() -> None:
            both_loaded.wait()
            if getattr(thread_state, "role", None) == "loser":
                assert winner_committed.wait(timeout=5)

        monkeypatch.setattr(repository, "_before_compare_and_swap", interleave)

        def run(role: str, operation: Callable[[], object]) -> object:
            thread_state.role = role
            try:
                result = operation()
                if role == "winner":
                    winner_committed.set()
                return result
            except RuntimeRecordRevisionConflict as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as pool:
            winner_result, loser_result = list(
                pool.map(lambda item: run(*item), [("winner", winner), ("loser", loser)])
            )
        assert not isinstance(winner_result, RuntimeRecordRevisionConflict)
        assert isinstance(loser_result, RuntimeRecordRevisionConflict)
        assert loser_result.expected_revision == 0
        assert loser_result.actual_revision == 1

    assert_race(
        store.traces,
        lambda: store.traces.finish(created_span.span_id, TraceSpanStatus.COMPLETED, 0),
        lambda: store.traces.finish(created_span.span_id, TraceSpanStatus.COMPLETED, 0),
    )
    assert_race(
        store.worktrees,
        lambda: store.worktrees.update(created_worktree.worktree_id, 0, status=WorktreeStatus.READY),
        lambda: store.worktrees.update(created_worktree.worktree_id, 0, status=WorktreeStatus.READY),
    )


def test_worktree_create_update_get_and_stale_revision(store: SQLAlchemyStateStore) -> None:
    details = {"lease": {"owner": "agent-1"}}
    created = store.worktrees.create(
        WorktreeRecord(
            worktree_id="wt-1",
            root_session_id="root-1",
            agent_id="agent-1",
            repository_root="/repo",
            canonical_path="/repo/.worktrees/wt-1",
            branch="codex/wt-1",
            base_commit="abc123",
            status=WorktreeStatus.CREATING,
            details=details,
        )
    )
    details["lease"]["owner"] = "changed"
    updated = store.worktrees.update(
        created.worktree_id,
        created.revision,
        status=WorktreeStatus.READY,
        details={"lease": {"owner": "agent-1"}, "prepared": True},
    )
    assert updated.status is WorktreeStatus.READY
    assert store.worktrees.get(created.worktree_id) == updated
    assert [item.worktree_id for item in store.worktrees.list("root-1", agent_id="agent-1")] == [
        "wt-1"
    ]
    with pytest.raises(RuntimeRecordRevisionConflict):
        store.worktrees.update(created.worktree_id, created.revision, branch="stale")


def test_stale_runtime_updates_report_winner_revision(store: SQLAlchemyStateStore) -> None:
    agent_record = store.agents.create(agent("cas-agent"))
    store.agents.transition(agent_record.agent_id, AgentStatus.RUNNING, 0)
    with pytest.raises(RuntimeRecordRevisionConflict) as agent_error:
        store.agents.transition(agent_record.agent_id, AgentStatus.FAILED, 0)

    span = store.traces.start(
        TraceSpanRecord(span_id="cas-span", root_session_id="root-1", kind="tool", name="request")
    )
    store.traces.finish(span.span_id, TraceSpanStatus.COMPLETED, 0)
    with pytest.raises(RuntimeRecordRevisionConflict) as trace_error:
        store.traces.finish(span.span_id, TraceSpanStatus.FAILED, 0)

    worktree = store.worktrees.create(
        WorktreeRecord(
            worktree_id="cas-wt",
            root_session_id="root-1",
            repository_root="/repo",
            canonical_path="/repo/wt",
            branch="branch",
            base_commit="abc",
            status=WorktreeStatus.CREATING,
        )
    )
    store.worktrees.update(worktree.worktree_id, 0, status=WorktreeStatus.READY)
    with pytest.raises(RuntimeRecordRevisionConflict) as worktree_error:
        store.worktrees.update(worktree.worktree_id, 0, branch="stale")

    assert agent_error.value.actual_revision == 1
    assert trace_error.value.actual_revision == 1
    assert worktree_error.value.actual_revision == 1


def test_negative_submillisecond_trace_duration_is_rejected() -> None:
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="finished_at"):
        TraceSpanRecord(
            span_id="negative",
            root_session_id="root-1",
            kind="tool",
            name="request",
            status=TraceSpanStatus.COMPLETED,
            started_at=started_at,
            finished_at=started_at - timedelta(microseconds=500),
        )
