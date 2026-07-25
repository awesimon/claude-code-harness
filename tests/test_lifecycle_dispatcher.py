from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from harness.context import CancellationToken
from state_core import HookAsyncMode, HookDefinitionRecord, HookInvocationStatus
from state_core.sqlalchemy_store import Base, SQLAlchemyStateStore


def _store(tmp_path: Path) -> SQLAlchemyStateStore:
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{tmp_path / 'lifecycle.db'}")
    Base.metadata.create_all(engine)
    return SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))


def _definition(root: str, hook_id: str, order: int, kind: str, config: dict, **kw):
    return HookDefinitionRecord(
        definition_id=hook_id,
        root_session_id=root,
        event=kw.pop("event", "PreToolUse"),
        matcher=kw.pop("matcher", None),
        runner_kind=kind,
        runner_config=config,
        source="test",
        order=order,
        **kw,
    )


@pytest.mark.asyncio
async def test_lifecycle_normalizes_every_event_and_rejects_secrets(tmp_path: Path) -> None:
    from harness.hooks import HookEvent
    from harness.lifecycle import LifecycleDispatcher

    seen = []

    class Hooks:
        async def dispatch(self, envelope, cancellation=None):
            seen.append(envelope)
            return envelope

    lifecycle = LifecycleDispatcher(Hooks(), root_session_id="root", cwd=tmp_path)
    for index, event in enumerate(HookEvent):
        await lifecycle.emit(
            event,
            {"message": event.value},
            correlation_id=f"correlation-{index}",
            permission_mode="default",
            transcript_position=index,
            tool_call_id="tool-1" if event is HookEvent.PRE_TOOL_USE else None,
        )

    assert [item["hook_event_name"] for item in seen] == [event.value for event in HookEvent]
    assert all(item["root_session_id"] == "root" for item in seen)
    assert all(item["cwd"] == str(tmp_path.resolve()) for item in seen)
    assert seen[0]["correlation_id"] == "correlation-0"
    await lifecycle.emit(HookEvent.POST_TOOL_USE, {"token_count": 12})
    assert seen[-1]["payload"]["token_count"] == 12
    with pytest.raises(ValueError, match="sensitive"):
        await lifecycle.emit(HookEvent.NOTIFICATION, {"api_key": "secret"})


@pytest.mark.asyncio
async def test_matching_hooks_run_concurrently_but_aggregate_in_definition_order(
    tmp_path: Path,
) -> None:
    from harness.hooks import HookDecision, HookDispatcher

    store = _store(tmp_path)
    active = 0
    peak = 0

    async def prompt(envelope, config, cancellation):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(config["delay"])
        active -= 1
        return config["result"]

    store.hook_definitions.create(
        _definition(
            "root",
            "first",
            1,
            "prompt",
            {
                "delay": 0.04,
                "result": {"decision": "allow", "updated_input": {"same": "first", "a": 1}},
            },
        )
    )
    store.hook_definitions.create(
        _definition(
            "root",
            "second",
            2,
            "prompt",
            {
                "delay": 0.01,
                "result": {
                    "permission_decision": "deny",
                    "updated_input": {"same": "second", "b": 2},
                },
            },
        )
    )
    dispatcher = HookDispatcher(
        store.hook_definitions,
        store.hook_invocations,
        root_session_id="root",
        owner_token="owner",
        prompt_runner=prompt,
    )

    result = await dispatcher.dispatch(
        {
            "hook_event_name": "PreToolUse",
            "correlation_id": "call-1",
            "root_session_id": "root",
            "tool_name": "bash",
            "payload": {"tool_input": {"original": True}},
        }
    )

    assert peak == 2
    assert result.decision is HookDecision.BLOCK
    assert result.permission_decision == "deny"
    assert result.input_patch == {"same": "second", "a": 1, "b": 2}
    assert result.executed_hook_ids == ("first", "second")


@pytest.mark.asyncio
async def test_command_prompt_http_agent_timeout_cancel_once_and_async_rewake(
    tmp_path: Path,
) -> None:
    from harness.hooks import HookDispatcher

    store = _store(tmp_path)
    script = tmp_path / "hook.py"
    script.write_text(
        "import json,sys; data=json.load(sys.stdin); "
        "print(json.dumps({'metadata': {'kind': data['hook_event_name']}}))",
        encoding="utf-8",
    )
    command = f'"{sys.executable}" "{script}"'
    calls: list[str] = []
    rewakes: list[dict] = []

    async def prompt(envelope, config, cancellation):
        calls.append("prompt")
        return {"metadata": {"prompt": True}}

    async def http(envelope, config, cancellation):
        calls.append("http")
        return {"metadata": {"http": True}}

    async def agent(envelope, config, runner_context):
        calls.append("agent")
        assert runner_context.restricted is True
        assert runner_context.allowed_tools == ("read_file",)
        assert config["restricted_scope"] is False
        return {"metadata": {"agent": True}}

    definitions = [
        _definition("root", "command", 1, "command", {"command": command}),
        _definition("root", "prompt", 2, "prompt", {}, once=True),
        _definition("root", "http", 3, "http", {"url": "https://hooks.invalid"}),
        _definition(
            "root",
            "agent",
            4,
            "agent",
            {"restricted_scope": False, "allowed_tools": ["bash"]},
        ),
        _definition("root", "rewake", 5, "prompt", {}, async_mode=HookAsyncMode.ASYNC_REWAKE),
    ]
    for definition in definitions:
        store.hook_definitions.create(definition)
    dispatcher = HookDispatcher(
        store.hook_definitions,
        store.hook_invocations,
        root_session_id="root",
        owner_token="owner",
        prompt_runner=prompt,
        http_runner=http,
        agent_runner=agent,
        agent_tool_allowlist=("read_file",),
        notification_sink=rewakes.append,
    )

    first = await dispatcher.dispatch(
        {"hook_event_name": "PreToolUse", "correlation_id": "one", "root_session_id": "root"}
    )
    second = await dispatcher.dispatch(
        {"hook_event_name": "PreToolUse", "correlation_id": "two", "root_session_id": "root"}
    )
    await dispatcher.drain()

    assert first.executed_hook_ids == ("command", "prompt", "http", "agent", "rewake")
    assert second.executed_hook_ids == ("command", "http", "agent", "rewake")
    assert rewakes and rewakes[-1]["type"] == "hook_async_rewake"
    assert all(
        item.status in {HookInvocationStatus.SUCCEEDED, HookInvocationStatus.INTERRUPTED}
        for item in store.hook_invocations.list("root")
    )
    restarted = _store(tmp_path)
    durable_rewakes = restarted.outbox.list("root", kind="hook_async_rewake")
    assert any(event.aggregate_id in first.async_invocation_ids for event in durable_rewakes)

    slow = _store(tmp_path / "slow")
    slow.hook_definitions.create(_definition("slow-root", "slow", 1, "prompt", {}, timeout_ms=20))

    async def wait_forever(envelope, config, cancellation):
        await asyncio.sleep(60)

    timed = HookDispatcher(
        slow.hook_definitions,
        slow.hook_invocations,
        root_session_id="slow-root",
        owner_token="owner",
        prompt_runner=wait_forever,
    )
    result = await timed.dispatch(
        {
            "hook_event_name": "PreToolUse",
            "correlation_id": "timeout",
            "root_session_id": "slow-root",
        }
    )
    assert result.failures[0].category == "timed_out"

    token = CancellationToken()
    task = asyncio.create_task(
        timed.dispatch(
            {
                "hook_event_name": "PreToolUse",
                "correlation_id": "cancel",
                "root_session_id": "slow-root",
            },
            cancellation=token,
        )
    )
    await asyncio.sleep(0.005)
    token.cancel()
    cancelled = await task
    assert cancelled.failures[0].category == "cancelled"


@pytest.mark.asyncio
async def test_feedback_attempt_limit_and_interrupted_invocations_are_not_replayed(
    tmp_path: Path,
) -> None:
    from harness.hooks import HookDispatcher

    store = _store(tmp_path)

    async def blocking(envelope, config, cancellation):
        return {"decision": "block", "reason": "more work"}

    store.hook_definitions.create(
        _definition("root", "feedback", 1, "prompt", {}, event="Stop", idempotent=False)
    )
    dispatcher = HookDispatcher(
        store.hook_definitions,
        store.hook_invocations,
        root_session_id="root",
        owner_token="owner",
        prompt_runner=blocking,
        feedback_attempt_limit=2,
    )
    envelope = {"hook_event_name": "Stop", "correlation_id": "turn", "root_session_id": "root"}
    assert (await dispatcher.dispatch(envelope)).blocked is True
    assert (await dispatcher.dispatch({**envelope, "feedback_attempt": 2})).blocked is True
    limited = await dispatcher.dispatch({**envelope, "feedback_attempt": 3})
    assert limited.attempt_limit_reached is True

    interrupted = store.hook_invocations.list("root")[0]
    # A repeated correlation is an audit replay, never a new side effect.
    replay = await dispatcher.dispatch(envelope)
    assert replay.executed_hook_ids == ()
    assert store.hook_invocations.get(interrupted.invocation_id) is not None


@pytest.mark.asyncio
async def test_node_hook_specific_output_is_normalized_and_pre_events_fail_closed(
    tmp_path: Path,
) -> None:
    from harness.hooks import HookDispatcher

    store = _store(tmp_path)

    async def node_output(envelope, config, cancellation):
        if config.get("fail"):
            raise RuntimeError("runner failed")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "allow",
                    "updatedInput": {"command": "echo safe"},
                    "updatedPermissions": [{"type": "setMode", "mode": "plan"}],
                },
            }
        }

    store.hook_definitions.create(
        _definition(
            "root",
            "node",
            1,
            "prompt",
            {},
            event="PermissionRequest",
        )
    )
    store.hook_definitions.create(
        _definition(
            "root",
            "failure",
            2,
            "prompt",
            {"fail": True},
            event="PermissionRequest",
        )
    )
    dispatcher = HookDispatcher(
        store.hook_definitions,
        store.hook_invocations,
        root_session_id="root",
        owner_token="owner",
        prompt_runner=node_output,
    )

    result = await dispatcher.dispatch(
        {
            "hook_event_name": "PermissionRequest",
            "correlation_id": "permission",
            "root_session_id": "root",
        }
    )

    assert result.blocked is True
    assert result.permission_decision == "allow"
    assert result.input_patch == {"command": "echo safe"}
    assert result.permission_updates == ({"type": "setMode", "mode": "plan"},)
    assert result.failures[0].category == "runner_failed"


@pytest.mark.asyncio
async def test_runner_cannot_recursively_trigger_the_same_hook_event(tmp_path: Path) -> None:
    from harness.hooks import HookDispatcher

    store = _store(tmp_path)
    calls = 0
    dispatcher = None

    async def recursive(envelope, config, cancellation):
        nonlocal calls
        calls += 1
        nested = await dispatcher.dispatch(
            {**envelope, "correlation_id": f"nested-{calls}"}, cancellation
        )
        assert nested.executed_hook_ids == ()
        return {}

    store.hook_definitions.create(_definition("root", "recursive", 1, "prompt", {}))
    dispatcher = HookDispatcher(
        store.hook_definitions,
        store.hook_invocations,
        root_session_id="root",
        owner_token="owner",
        prompt_runner=recursive,
    )

    await dispatcher.dispatch(
        {
            "hook_event_name": "PreToolUse",
            "correlation_id": "outer",
            "root_session_id": "root",
        }
    )

    assert calls == 1


@pytest.mark.asyncio
async def test_command_output_limit_terminates_continuous_writer_before_timeout(
    tmp_path: Path,
) -> None:
    from harness.hooks import HookDispatcher

    store = _store(tmp_path)
    script = tmp_path / "flood.py"
    script.write_text(
        "import os,sys,time\n"
        "while True:\n"
        " os.write(sys.stdout.fileno(), b'x' * 8192)\n"
        " time.sleep(0.001)\n",
        encoding="utf-8",
    )
    store.hook_definitions.create(
        _definition(
            "root",
            "flood",
            1,
            "command",
            {"command": f'"{sys.executable}" "{script}"', "output_limit": 1024},
            timeout_ms=5_000,
        )
    )
    dispatcher = HookDispatcher(
        store.hook_definitions,
        store.hook_invocations,
        root_session_id="root",
        owner_token="owner",
    )

    started = asyncio.get_running_loop().time()
    result = await dispatcher.dispatch(
        {
            "hook_event_name": "PreToolUse",
            "correlation_id": "flood",
            "root_session_id": "root",
        }
    )

    assert result.failures[0].category == "output_limit"
    assert asyncio.get_running_loop().time() - started < 1


@pytest.mark.asyncio
async def test_direct_dispatch_cancellation_stops_runner_and_persists_cancelled(
    tmp_path: Path,
) -> None:
    from harness.hooks import HookDispatcher

    store = _store(tmp_path)
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def prompt(envelope, config, cancellation):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    store.hook_definitions.create(_definition("root", "cancel", 1, "prompt", {}))
    dispatcher = HookDispatcher(
        store.hook_definitions,
        store.hook_invocations,
        root_session_id="root",
        owner_token="owner",
        prompt_runner=prompt,
    )
    dispatch = asyncio.create_task(
        dispatcher.dispatch(
            {
                "hook_event_name": "PreToolUse",
                "correlation_id": "direct-cancel",
                "root_session_id": "root",
            }
        )
    )
    await asyncio.wait_for(started.wait(), 0.25)
    dispatch.cancel()

    with pytest.raises(asyncio.CancelledError):
        await dispatch
    await asyncio.wait_for(stopped.wait(), 0.25)
    invocation = store.hook_invocations.list("root")[0]
    assert invocation.status is HookInvocationStatus.CANCELLED
    assert invocation.lease_owner is None
    assert invocation.lease_expires_at is None


@pytest.mark.asyncio
async def test_direct_dispatch_cancellation_terminates_command_process_group(
    tmp_path: Path,
) -> None:
    from harness.hooks import HookDispatcher

    store = _store(tmp_path)
    ready = tmp_path / "child-ready"
    stopped = tmp_path / "child-stopped"
    child = tmp_path / "child.py"
    child.write_text(
        "import pathlib,signal,sys,time\n"
        "ready=pathlib.Path(sys.argv[1]); stopped=pathlib.Path(sys.argv[2])\n"
        "def stop(*_):\n"
        " stopped.write_text('stopped')\n"
        " raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "ready.write_text('ready')\n"
        "while True: time.sleep(1)\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess,sys,time\n"
        "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]])\n"
        "while True: time.sleep(1)\n",
        encoding="utf-8",
    )
    command = f'"{sys.executable}" "{parent}" "{child}" "{ready}" "{stopped}"'
    store.hook_definitions.create(
        _definition("root", "command-cancel", 1, "command", {"command": command})
    )
    dispatcher = HookDispatcher(
        store.hook_definitions,
        store.hook_invocations,
        root_session_id="root",
        owner_token="owner",
    )
    dispatch = asyncio.create_task(
        dispatcher.dispatch(
            {
                "hook_event_name": "PreToolUse",
                "correlation_id": "command-cancel",
                "root_session_id": "root",
                "cwd": str(tmp_path),
            }
        )
    )
    for _ in range(50):
        if ready.exists():
            break
        await asyncio.sleep(0.01)
    assert ready.exists()
    dispatch.cancel()

    with pytest.raises(asyncio.CancelledError):
        await dispatch
    for _ in range(50):
        if stopped.exists():
            break
        await asyncio.sleep(0.01)
    assert stopped.read_text(encoding="utf-8") == "stopped"
    assert store.hook_invocations.list("root")[0].status is HookInvocationStatus.CANCELLED
