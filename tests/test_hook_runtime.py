from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from harness.context import CancellationToken
from state_core.sqlalchemy_store import Base, SQLAlchemyStateStore


def _store(tmp_path: Path) -> SQLAlchemyStateStore:
    engine = create_engine(f"sqlite:///{tmp_path / 'hooks.db'}")
    Base.metadata.create_all(engine)
    return SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))


def _script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{path}"'


def _context(tmp_path: Path, *, cancellation: CancellationToken | None = None):
    from harness.hooks import HookContext

    return HookContext(
        session_id="session-1",
        cwd=tmp_path,
        cancellation=cancellation or CancellationToken(),
    )


def _definition(command: str, **overrides):
    from harness.hooks import HookDefinition, HookEvent

    values = {
        "hook_id": "hook-1",
        "event": HookEvent.PRE_TOOL_USE,
        "command": command,
    }
    values.update(overrides)
    return HookDefinition(**values)


@pytest.mark.asyncio
async def test_pre_tool_hook_updates_input_and_receives_structured_stdin(tmp_path: Path) -> None:
    from harness.hooks import HookDecision, HookRuntime

    command = _script(
        tmp_path,
        "update.py",
        """import json, sys
payload = json.load(sys.stdin)
assert payload["hook_event_name"] == "PreToolUse"
assert payload["tool_name"] == "read_file"
assert payload["tool_input"] == {"path": "a.txt"}
print(json.dumps({"decision": "allow", "updated_input": {"path": "b.txt"}}))
""",
    )
    runtime = HookRuntime([_definition(command)])

    result = await runtime.run_pre_tool(
        "read_file", {"path": "a.txt"}, _context(tmp_path)
    )

    assert result.decision is HookDecision.ALLOW
    assert result.input == {"path": "b.txt"}
    assert result.failures == ()


@pytest.mark.asyncio
async def test_pre_hook_blocks_while_failing_post_hook_is_observational(tmp_path: Path) -> None:
    from harness.hooks import HookDecision, HookEvent, HookRuntime

    block = _script(
        tmp_path,
        "block.py",
        'import json; print(json.dumps({"decision": "block", "reason": "policy"}))',
    )
    fail = _script(tmp_path, "fail.py", "raise SystemExit(7)")
    runtime = HookRuntime(
        [
            _definition(block),
            _definition(
                fail,
                hook_id="hook-post",
                event=HookEvent.POST_TOOL_USE,
            ),
        ]
    )

    blocked = await runtime.run_pre_tool("bash", {"command": "false"}, _context(tmp_path))
    observed = await runtime.run_post_tool(
        "read_file", {"path": "a.txt"}, {"content": "ok"}, _context(tmp_path)
    )

    assert blocked.decision is HookDecision.BLOCK
    assert blocked.reason == "policy"
    assert observed.result == {"content": "ok"}
    assert observed.failures[0].hook_id == "hook-post"
    assert observed.failures[0].category == "nonzero_exit"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "body", "category", "overrides"),
    [
        ("malformed.py", 'print("not-json")', "malformed_output", {}),
        (
            "multiple.py",
            'print("{}\\n{}")',
            "malformed_output",
            {},
        ),
        (
            "large.py",
            'print("x" * 10000)',
            "output_limit",
            {"output_limit": 128},
        ),
        (
            "slow.py",
            "import time; time.sleep(2)",
            "timed_out",
            {"timeout": 0.05},
        ),
    ],
)
async def test_pre_hook_failures_fail_closed(
    tmp_path: Path,
    name: str,
    body: str,
    category: str,
    overrides: dict[str, object],
) -> None:
    from harness.hooks import HookDecision, HookRuntime

    runtime = HookRuntime([_definition(_script(tmp_path, name, body), **overrides)])
    result = await runtime.run_pre_tool("bash", {}, _context(tmp_path))

    assert result.decision is HookDecision.BLOCK
    assert result.failures[0].category == category


@pytest.mark.asyncio
async def test_matcher_filters_hooks_and_environment_is_allowlisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness.hooks import HookRuntime

    monkeypatch.setenv("HOOK_TEST_SECRET", "must-not-leak")
    command = _script(
        tmp_path,
        "env.py",
        """import json, os
assert "HOOK_TEST_SECRET" not in os.environ
print(json.dumps({"decision": "allow", "updated_input": {"matched": True}}))
""",
    )
    runtime = HookRuntime([_definition(command, matcher="^read(_file)?$")])

    skipped = await runtime.run_pre_tool("bash", {}, _context(tmp_path))
    matched = await runtime.run_pre_tool("read_file", {}, _context(tmp_path))

    assert skipped.input == {}
    assert matched.input == {"matched": True}


@pytest.mark.asyncio
async def test_cancellation_terminates_hook_and_fails_closed(tmp_path: Path) -> None:
    from harness.hooks import HookDecision, HookRuntime

    token = CancellationToken()
    runtime = HookRuntime(
        [_definition(_script(tmp_path, "cancel.py", "import time; time.sleep(10)"))]
    )
    task = asyncio.create_task(
        runtime.run_pre_tool("bash", {}, _context(tmp_path, cancellation=token))
    )
    await asyncio.sleep(0.05)
    token.cancel()
    result = await asyncio.wait_for(task, 1)

    assert result.decision is HookDecision.BLOCK
    assert result.failures[0].category == "cancelled"


@pytest.mark.asyncio
async def test_hook_execution_is_recursion_safe(tmp_path: Path) -> None:
    from harness.hooks import HookRuntime

    runtime = HookRuntime(
        [_definition(_script(tmp_path, "allow.py", 'print("{\\\"decision\\\": \\\"allow\\\"}")'))]
    )

    async with runtime.execution_guard():
        result = await runtime.run_pre_tool("bash", {"nested": True}, _context(tmp_path))

    assert result.input == {"nested": True}
    assert result.executed_hook_ids == ()


@pytest.mark.asyncio
async def test_configuration_and_bounded_events_are_durable(tmp_path: Path) -> None:
    from harness.hooks import HookEvent, HookRuntime

    store = _store(tmp_path)
    command = _script(
        tmp_path,
        "allow.py",
        'import json; print(json.dumps({"decision": "allow"}))',
    )
    first = HookRuntime(
        [_definition(command)],
        metadata_repository=store.metadata,
        root_session_id="session-1",
        max_events=2,
    )
    added = first.add(
        HookEvent.POST_TOOL_USE,
        command,
        matcher="read_file",
    )
    for _ in range(3):
        await first.run_pre_tool("bash", {}, _context(tmp_path))

    resumed = HookRuntime(
        None,
        metadata_repository=store.metadata,
        root_session_id="session-1",
        max_events=2,
    )

    assert [hook.hook_id for hook in resumed.list()] == ["hook-1", added.hook_id]
    assert len(resumed.events()) == 2
    assert all("stdout" not in event and "tool_input" not in event for event in resumed.events())
    assert resumed.remove(0).hook_id == "hook-1"
    assert [hook.hook_id for hook in HookRuntime(
        None,
        metadata_repository=store.metadata,
        root_session_id="session-1",
    ).list()] == [added.hook_id]


@pytest.mark.asyncio
async def test_public_hook_tools_preserve_list_add_remove_shapes(tmp_path: Path) -> None:
    from harness.hooks import HookRuntime
    from tools.hooks_tools import HooksAddTool, HooksListTool, HooksRemoveTool

    store = _store(tmp_path)
    runtime = HookRuntime(
        [], metadata_repository=store.metadata, root_session_id="session-1"
    )
    context = {"session_harness": SimpleNamespace(hooks=runtime)}

    added = await HooksAddTool().run(
        {"event": "PreToolUse", "command": "true", "matcher": "bash"}, context
    )
    listed = await HooksListTool().run({}, context)
    removed = await HooksRemoveTool().run({"index": 0}, context)

    assert added.success is True
    assert set(added.data) >= {"event", "command", "matcher"}
    assert listed.data["count"] == 1
    assert listed.data["hooks"][0]["index"] == 0
    assert removed.data == {"index": 0}
