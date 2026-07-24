from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from harness import PermissionMode, RuntimeContext, SessionHarnessFactory, ToolRuntime
from harness.hooks import HookDecision, PostHookResult, PreHookResult
from models import Base
from state_core import EventType, SessionRuntimeFactory, SQLAlchemyStateStore
from tools.base import Tool, ToolResult
from tools.base import ToolValidationError


@dataclass
class _Input:
    value: int


class _Tool(Tool[_Input, dict]):
    name = "sample"
    description = "sample"
    input_type = _Input

    def __init__(self, calls: list[str], output=None) -> None:
        super().__init__()
        self.calls = calls
        self.output = {"value": 2} if output is None else output

    async def validate(self, input_data: _Input):
        self.calls.append("validate")
        if type(input_data.value) is not int:
            return ToolValidationError("value must be an integer")
        return None

    async def execute(self, input_data: _Input) -> ToolResult:
        self.calls.append("execute")
        return ToolResult.ok(self.output)

    def is_read_only(self) -> bool:
        return True


class _Registry:
    def __init__(self, tool: Tool) -> None:
        self.tool = tool

    def resolve_name(self, name: str):
        return "sample" if name in {"sample", "Sample"} else None

    def get(self, name: str):
        return self.tool if name == "sample" else None


class _Deferred:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def require_active(self, name: str, agent_id: str | None = None) -> None:
        self.calls.append("deferred")


class _Hooks:
    def __init__(self, calls: list[str], updated=None) -> None:
        self.calls = calls
        self.updated = updated

    async def run_pre_tool(self, name, input_data, context):
        self.calls.append("pre_hook")
        return PreHookResult(
            HookDecision.ALLOW,
            dict(input_data) if self.updated is None else dict(self.updated),
        )

    async def run_post_tool(self, name, input_data, result, context, *, failed=False):
        self.calls.append("post_hook")
        return PostHookResult(dict(result))


class _Permission:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.seen = None

    async def authorize(self, tool, name, input_data, context):
        self.calls.append("permission")
        self.seen = dict(input_data)
        return True, "allowed"


class _Reservation:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def consume(self, usage=None) -> None:
        self.calls.append("consume")

    def release(self) -> None:
        self.calls.append("release")


class _Budget:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def reserve_tool_call(self, *, agent_id=None):
        self.calls.append("reserve")
        return _Reservation(self.calls)


class _Persister:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def persist(self, **values) -> None:
        self.calls.append("persist")


class _ExplodingHooks:
    def __init__(self, *, post: bool = False) -> None:
        self.post = post

    async def run_pre_tool(self, name, input_data, context):
        if not self.post:
            raise RuntimeError("pre failed")
        return PreHookResult(HookDecision.ALLOW, dict(input_data))

    async def run_post_tool(self, name, input_data, result, context, *, failed=False):
        raise RuntimeError("post failed")


@pytest.mark.asyncio
async def test_pipeline_order_and_hook_input_update(tmp_path: Path) -> None:
    calls: list[str] = []

    def normalize(value):
        calls.append("normalize")
        return value

    permission = _Permission(calls)
    runtime = ToolRuntime(
        _Registry(_Tool(calls)),
        permission_policy=permission,
        deferred_registry=_Deferred(calls),
        hook_runtime=_Hooks(calls, updated={"value": 2}),
        budget_controller=_Budget(calls),
        result_normalizer=normalize,
        persister=_Persister(calls),
    )

    execution = await runtime.execute(
        "Sample",
        {"value": 1},
        RuntimeContext(workspace_root=tmp_path, permission_mode=PermissionMode.BYPASS),
    )

    assert execution.result.success is True
    assert permission.seen == {"value": 2}
    assert calls == [
        "deferred",
        "validate",
        "pre_hook",
        "permission",
        "reserve",
        "execute",
        "normalize",
        "post_hook",
        "consume",
        "persist",
    ]


@pytest.mark.asyncio
async def test_validation_and_serialization_failures_have_one_terminal_persist(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    persister = _Persister(calls)
    runtime = ToolRuntime(
        _Registry(_Tool(calls, output=object())),
        permission_policy=_Permission(calls),
        persister=persister,
    )

    invalid = await runtime.execute(
        "sample", {"value": "wrong"}, RuntimeContext(workspace_root=tmp_path)
    )
    serialization = await runtime.execute(
        "sample", {"value": 1}, RuntimeContext(workspace_root=tmp_path)
    )

    assert invalid.result.success is False
    assert serialization.result.success is False
    assert calls.count("persist") == 2


@pytest.mark.asyncio
async def test_default_persister_writes_exact_tool_call_result_pair(tmp_path: Path) -> None:
    calls: list[str] = []
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline.db'}")
    Base.metadata.create_all(engine)
    store = SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))
    harness = SessionHarnessFactory(
        SessionRuntimeFactory(store),
        workspace_root=tmp_path,
        tool_registry=_Registry(_Tool(calls)),
        permission_mode=PermissionMode.BYPASS,
    ).create("root")

    await harness.tool_runtime.execute(
        "sample",
        {"value": 1},
        harness.runtime_context,
        tool_call_id="call-1",
    )

    events = [
        event
        for event in harness.session_runtime.events()
        if event.event_type in {EventType.TOOL_CALL, EventType.TOOL_RESULT}
    ]
    assert [event.event_type for event in events] == [
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
    ]
    assert {event.payload["toolCallId"] for event in events} == {"call-1"}


@pytest.mark.asyncio
async def test_pre_hook_runtime_error_fails_closed_and_post_error_fails_open(
    tmp_path: Path,
) -> None:
    from harness import TerminationReason

    pre_calls: list[str] = []
    pre = ToolRuntime(
        _Registry(_Tool(pre_calls)),
        hook_runtime=_ExplodingHooks(),
        persister=_Persister(pre_calls),
    )
    blocked = await pre.execute(
        "sample", {"value": 1}, RuntimeContext(workspace_root=tmp_path)
    )

    post_calls: list[str] = []
    post = ToolRuntime(
        _Registry(_Tool(post_calls)),
        permission_policy=_Permission(post_calls),
        hook_runtime=_ExplodingHooks(post=True),
        persister=_Persister(post_calls),
    )
    observed = await post.execute(
        "sample", {"value": 1}, RuntimeContext(workspace_root=tmp_path)
    )

    assert blocked.termination_reason is TerminationReason.HOOK_BLOCKED
    assert "execute" not in pre_calls
    assert observed.result.success is True
