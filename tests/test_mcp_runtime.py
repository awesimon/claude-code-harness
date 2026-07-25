from __future__ import annotations

import sys
import asyncio
import os
import socket
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from harness import SessionHarnessFactory
from harness.mcp import (
    MCPConnectionManager,
    MCPServerConfig,
    MCPServerStatus,
    MCPTransport,
)
from state_core import EventType, SessionRuntimeFactory, SQLAlchemyStateStore
from state_core.sqlalchemy_store import Base


def _harness(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp.db'}")
    Base.metadata.create_all(engine)
    store = SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))
    harness = SessionHarnessFactory(
        SessionRuntimeFactory(store), workspace_root=tmp_path
    ).create("mcp-session")
    return harness, store


def _stdio_config(tmp_path: Path) -> MCPServerConfig:
    return MCPServerConfig(
        name="test",
        transport=MCPTransport.STDIO,
        command=sys.executable,
        args=(str(Path(__file__).with_name("mcp_test_server.py")),),
        cwd=tmp_path,
    )


@asynccontextmanager
async def _http_server():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {
        **os.environ,
        "MCP_TEST_PORT": str(port),
        "MCP_TEST_TRANSPORT": "streamable-http",
    }
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("mcp_test_server.py"))],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError("MCP HTTP test server exited during startup")
                await asyncio.sleep(0.02)
                continue
            writer.close()
            await writer.wait_closed()
            break
        else:
            raise RuntimeError("MCP HTTP test server did not start")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


@pytest.mark.asyncio
async def test_stdio_server_discovery_call_resource_and_disconnect(tmp_path: Path) -> None:
    harness, _ = _harness(tmp_path)
    manager = MCPConnectionManager(harness)

    connected = await manager.connect(_stdio_config(tmp_path))
    tools = await manager.list_tools("test")
    result = await manager.call_tool("test", "echo", {"text": "ok"})
    resources = await manager.list_resources("test")
    content = await manager.read_resource("test", "test://value")
    disconnected = await manager.disconnect("test")

    assert connected.status is MCPServerStatus.CONNECTED
    assert [tool.name for tool in tools] == ["echo", "fail", "slow"]
    assert result == {"text": "ok"}
    assert resources[0].uri == "test://value"
    assert content.text == "value"
    assert disconnected.status is MCPServerStatus.DISCONNECTED


@pytest.mark.asyncio
async def test_status_and_safe_config_digest_are_durable(tmp_path: Path) -> None:
    harness, store = _harness(tmp_path)
    manager = MCPConnectionManager(harness)
    config = _stdio_config(tmp_path)

    await manager.connect(config)
    status = manager.status("test")
    snapshot = store.metadata.get(harness.root_session_id, "mcp.runtime")
    await manager.close()

    assert status is not None and status.status is MCPServerStatus.CONNECTED
    assert snapshot is not None
    assert snapshot.snapshot["servers"]["test"]["config_digest"] == config.digest()
    assert "command" not in snapshot.snapshot["servers"]["test"]
    assert [event.event_type for event in harness.session_runtime.events()] == [
        EventType.MCP_CONNECTED,
        EventType.MCP_DISCONNECTED,
    ]


@pytest.mark.asyncio
async def test_child_server_scope_is_additive_and_isolated(tmp_path: Path) -> None:
    harness, _ = _harness(tmp_path)
    root = MCPConnectionManager(harness)
    child_harness = harness.child("child")
    child = MCPConnectionManager(child_harness, parent=root)

    await root.connect(_stdio_config(tmp_path))
    child_config = MCPServerConfig(
        **{
            **_stdio_config(tmp_path).to_dict(include_secrets=True),
            "name": "child-test",
        }
    )
    await child.connect(child_config)

    assert [server.name for server in child.list_servers()] == ["child-test", "test"]
    assert [server.name for server in root.list_servers()] == ["test"]

    await child.close()
    assert root.status("test").status is MCPServerStatus.CONNECTED
    await root.close()


@pytest.mark.asyncio
async def test_streamable_http_transport_uses_real_server(tmp_path: Path) -> None:
    harness, _ = _harness(tmp_path)
    async with _http_server() as url:
        manager = MCPConnectionManager(harness)
        await manager.connect(
            MCPServerConfig(
                name="http-test",
                transport=MCPTransport.STREAMABLE_HTTP,
                url=url,
            )
        )

        assert await manager.call_tool("http-test", "echo", {"text": "http"}) == {
            "text": "http"
        }
        await manager.close()


@pytest.mark.asyncio
async def test_timeout_cancels_request_without_losing_connection(tmp_path: Path) -> None:
    harness, _ = _harness(tmp_path)
    manager = MCPConnectionManager(harness)
    config = _stdio_config(tmp_path)
    config = MCPServerConfig(**{**config.to_dict(include_secrets=True), "timeout": 0.5})
    await manager.connect(config)

    with pytest.raises(asyncio.TimeoutError):
        await manager.call_tool("test", "slow", {"seconds": 1.5})

    assert await manager.call_tool("test", "echo", {"text": "alive"}) == {
        "text": "alive"
    }
    await manager.close()


@pytest.mark.asyncio
async def test_harness_cancellation_cancels_inflight_request(tmp_path: Path) -> None:
    harness, _ = _harness(tmp_path)
    manager = MCPConnectionManager(harness)
    await manager.connect(_stdio_config(tmp_path))
    call = asyncio.create_task(manager.call_tool("test", "slow", {"seconds": 2}))
    await asyncio.sleep(0.05)

    harness.runtime_context.cancellation.cancel()

    with pytest.raises(asyncio.CancelledError):
        await call
    await manager.close()


@pytest.mark.asyncio
async def test_public_tools_delegate_to_harness_manager(tmp_path: Path) -> None:
    harness, _ = _harness(tmp_path)
    assert harness.mcp is harness.mcp
    await harness.mcp.connect(_stdio_config(tmp_path))

    execution = await harness.tool_runtime.execute(
        "mcp_execute_tool",
        {"server": "test", "tool": "echo", "arguments": {"text": "tool"}},
        harness.runtime_context,
    )
    resources = await harness.tool_runtime.execute(
        "mcp_list_resources",
        {"server": "test"},
        harness.runtime_context,
    )

    assert execution.result.success is True
    assert execution.result.data == {"text": "tool"}
    assert resources.result.success is True
    assert resources.result.data[0]["uri"] == "test://value"
    await harness.mcp.close()


@pytest.mark.asyncio
async def test_tool_call_reconnects_dead_transport_once(tmp_path: Path) -> None:
    harness, _ = _harness(tmp_path)
    manager = MCPConnectionManager(harness)
    await manager.connect(_stdio_config(tmp_path))
    dead = manager._connections["test"]
    dead.task.cancel()
    await asyncio.gather(dead.task, return_exceptions=True)

    result = await manager.call_tool("test", "echo", {"text": "reconnected"})

    assert result == {"text": "reconnected"}
    assert manager._connections["test"] is not dead
    await manager.close()
