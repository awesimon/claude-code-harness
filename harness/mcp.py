"""Session-scoped MCP connections backed by the official Python SDK."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping

import httpx
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

from state_core import EventType, RuntimeRecordRevisionConflict

if TYPE_CHECKING:
    from .context import CancellationToken
    from .session import SessionHarness


MCP_RUNTIME_NAMESPACE = "mcp.runtime"


class MCPTransport(str, Enum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class MCPServerStatus(str, Enum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FAILED = "failed"
    DISCONNECTED = "disconnected"


class MCPError(RuntimeError):
    category = "mcp_unavailable"


class MCPServerNotFound(MCPError):
    pass


class MCPRemoteError(MCPError):
    category = "mcp_remote_error"


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: MCPTransport | str
    command: str | None = None
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: Path | str | None = None
    url: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("MCP server name must be non-empty")
        if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in self.name):
            raise ValueError("MCP server name contains unsafe characters")
        transport = MCPTransport(self.transport)
        object.__setattr__(self, "transport", transport)
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "env", dict(self.env))
        object.__setattr__(self, "headers", dict(self.headers))
        if isinstance(self.timeout, bool) or self.timeout <= 0:
            raise ValueError("MCP timeout must be positive")
        if transport is MCPTransport.STDIO:
            if not isinstance(self.command, str) or not self.command.strip():
                raise ValueError("stdio MCP servers require a command")
            if self.url is not None:
                raise ValueError("stdio MCP servers cannot define a URL")
        else:
            if not isinstance(self.url, str) or not self.url.startswith(("http://", "https://")):
                raise ValueError("streamable HTTP MCP servers require an HTTP URL")
            if self.command is not None:
                raise ValueError("HTTP MCP servers cannot define a command")

    def to_dict(self, *, include_secrets: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "name": self.name,
            "transport": self.transport.value,
            "command": self.command,
            "args": list(self.args),
            "cwd": str(self.cwd) if self.cwd is not None else None,
            "url": self.url,
            "timeout": float(self.timeout),
        }
        if include_secrets:
            value["env"] = dict(self.env)
            value["headers"] = dict(self.headers)
        return value

    def digest(self) -> str:
        encoded = json.dumps(
            self.to_dict(include_secrets=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class MCPToolDefinition:
    name: str
    description: str
    server: str
    input_schema: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPResourceDefinition:
    uri: str
    name: str
    server: str
    description: str | None = None
    mime_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPResourceContent:
    uri: str
    text: str | None = None
    blob: str | None = None
    mime_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPServerRecord:
    name: str
    transport: MCPTransport
    status: MCPServerStatus
    config_digest: str
    agent_id: str | None = None
    error: str | None = None


@dataclass
class _Request:
    operation: Callable[[ClientSession], Awaitable[Any]]
    future: asyncio.Future[Any]
    task: asyncio.Task[Any] | None = None
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True
        if self.task is not None and not self.task.done():
            self.task.cancel()
        if not self.future.done():
            self.future.cancel()


class _Connection:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.queue: asyncio.Queue[_Request | None] = asyncio.Queue()
        self.ready: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.task = asyncio.create_task(self._run(), name=f"mcp:{config.name}")

    async def start(self) -> Any:
        return await asyncio.wait_for(asyncio.shield(self.ready), self.config.timeout)

    async def request(
        self,
        operation: Callable[[ClientSession], Awaitable[Any]],
        *,
        timeout: float,
        cancellation: "CancellationToken | None",
    ) -> Any:
        future = asyncio.get_running_loop().create_future()
        request = _Request(operation, future)
        remove_callback = (
            cancellation.add_callback(request.cancel) if cancellation is not None else lambda: None
        )
        await self.queue.put(request)
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            request.cancel()
            raise
        finally:
            remove_callback()

    async def close(self) -> None:
        if self.task.done():
            await asyncio.gather(self.task, return_exceptions=True)
            return
        await self.queue.put(None)
        await self.task

    async def _run(self) -> None:
        try:
            async with AsyncExitStack() as stack:
                read, write = await self._open_transport(stack)
                session = await stack.enter_async_context(
                    ClientSession(
                        read,
                        write,
                        read_timeout_seconds=timedelta(seconds=self.config.timeout),
                    )
                )
                initialized = await session.initialize()
                if not self.ready.done():
                    self.ready.set_result(initialized)
                while True:
                    request = await self.queue.get()
                    if request is None:
                        break
                    if request.cancelled:
                        continue
                    request.task = asyncio.create_task(request.operation(session))
                    try:
                        value = await request.task
                    except BaseException as exc:
                        if not request.future.done():
                            request.future.set_exception(exc)
                    else:
                        if not request.future.done():
                            request.future.set_result(value)
        except BaseException as exc:
            if not self.ready.done():
                self.ready.set_exception(exc)
            while not self.queue.empty():
                request = self.queue.get_nowait()
                if request is not None and not request.future.done():
                    request.future.set_exception(MCPError("MCP connection closed"))
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def _open_transport(self, stack: AsyncExitStack):
        if self.config.transport is MCPTransport.STDIO:
            params = StdioServerParameters(
                command=self.config.command or "",
                args=list(self.config.args),
                env=dict(self.config.env) or None,
                cwd=self.config.cwd,
            )
            return await stack.enter_async_context(stdio_client(params))
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                headers=dict(self.config.headers),
                timeout=httpx.Timeout(self.config.timeout),
            )
        )
        read, write, _session_id = await stack.enter_async_context(
            streamable_http_client(self.config.url or "", http_client=client)
        )
        return read, write


class MCPConnectionManager:
    """Own live MCP handles for one root or child harness scope."""

    def __init__(
        self,
        harness: "SessionHarness",
        *,
        parent: "MCPConnectionManager | None" = None,
    ) -> None:
        self.harness = harness
        self.parent = parent
        self._connections: dict[str, _Connection] = {}
        self._configs: dict[str, MCPServerConfig] = {}
        self._records: dict[str, MCPServerRecord] = {}
        self._closed = False

    async def connect(self, config: MCPServerConfig) -> MCPServerRecord:
        if self._closed:
            raise MCPError("MCP manager is closed")
        if config.name in self._connections:
            await self.disconnect(config.name)
        self._configs[config.name] = config
        self._set_record(config, MCPServerStatus.CONNECTING)
        connection = _Connection(config)
        self._connections[config.name] = connection
        try:
            await connection.start()
        except BaseException as exc:
            self._connections.pop(config.name, None)
            record = self._set_record(config, MCPServerStatus.FAILED, error=type(exc).__name__)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise MCPError(f'MCP server "{config.name}" failed to connect') from exc
        record = self._set_record(config, MCPServerStatus.CONNECTED)
        try:
            definitions = await self.list_tools(config.name)
            self.harness.deferred_tools.register_mcp_tools(
                config.name, definitions
            )
        except BaseException:
            await self.disconnect(config.name)
            raise
        self.harness.session_runtime.append_event(
            EventType.MCP_CONNECTED,
            {"server": config.name, "transport": config.transport.value},
        )
        return record

    async def disconnect(self, server_name: str) -> MCPServerRecord:
        connection = self._connections.pop(server_name, None)
        config = self._configs.get(server_name)
        if config is None:
            raise MCPServerNotFound(f'MCP server "{server_name}" is not configured')
        if connection is not None:
            await connection.close()
        self.harness.deferred_tools.set_mcp_server_available(server_name, False)
        record = self._set_record(config, MCPServerStatus.DISCONNECTED)
        self.harness.session_runtime.append_event(
            EventType.MCP_DISCONNECTED,
            {"server": server_name},
        )
        return record

    def status(self, server_name: str) -> MCPServerRecord | None:
        record = self._records.get(server_name)
        if record is not None:
            return record
        return self.parent.status(server_name) if self.parent is not None else None

    def list_servers(self) -> list[MCPServerRecord]:
        records = list(self._records.values())
        names = set(self._records)
        if self.parent is not None:
            records.extend(item for item in self.parent.list_servers() if item.name not in names)
        return records

    async def list_tools(self, server_name: str | None = None) -> list[MCPToolDefinition]:
        names = [server_name] if server_name is not None else [item.name for item in self.list_servers()]
        tools: list[MCPToolDefinition] = []
        for name in names:
            result = await self._request(name, lambda session: session.list_tools())
            tools.extend(
                MCPToolDefinition(
                    name=tool.name,
                    description=tool.description or "",
                    server=name,
                    input_schema=dict(tool.inputSchema),
                    metadata=dict(tool.meta or {}),
                )
                for tool in result.tools
            )
        return tools

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: Mapping[str, Any]
    ) -> Any:
        started = time.monotonic()
        try:
            result = await self._request(
                server_name,
                lambda session: session.call_tool(tool_name, dict(arguments)),
                reconnect=True,
            )
        finally:
            self.harness.session_runtime.append_event(
                EventType.MCP_CALL,
                {
                    "server": server_name,
                    "tool": tool_name,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
        if result.isError:
            message = next(
                (item.text for item in result.content if getattr(item, "type", None) == "text"),
                "MCP tool call failed",
            )
            raise MCPRemoteError(message)
        if result.structuredContent is not None:
            return _json_value(result.structuredContent)
        return {
            "content": [_json_value(item.model_dump(by_alias=True, exclude_none=True, mode="json")) for item in result.content],
            "metadata": _json_value(result.meta or {}),
        }

    async def list_resources(
        self, server_name: str | None = None
    ) -> list[MCPResourceDefinition]:
        names = [server_name] if server_name is not None else [item.name for item in self.list_servers()]
        resources: list[MCPResourceDefinition] = []
        for name in names:
            result = await self._request(name, lambda session: session.list_resources())
            resources.extend(
                MCPResourceDefinition(
                    uri=str(resource.uri),
                    name=resource.name,
                    server=name,
                    description=resource.description,
                    mime_type=resource.mimeType,
                    metadata=dict(resource.meta or {}),
                )
                for resource in result.resources
            )
        return resources

    async def read_resource(self, server_name: str, uri: str) -> MCPResourceContent:
        result = await self._request(
            server_name, lambda session: session.read_resource(AnyUrl(uri))
        )
        if len(result.contents) != 1:
            raise MCPError("MCP resource returned an unsupported content count")
        content = result.contents[0]
        return MCPResourceContent(
            uri=str(content.uri),
            text=getattr(content, "text", None),
            blob=getattr(content, "blob", None),
            mime_type=content.mimeType,
            metadata=dict(content.meta or {}),
        )

    async def close(self) -> None:
        if self._closed:
            return
        for name in list(self._connections):
            await self.disconnect(name)
        self._closed = True

    async def _request(
        self,
        server_name: str,
        operation: Callable[[ClientSession], Awaitable[Any]],
        *,
        reconnect: bool = False,
    ) -> Any:
        connection = self._connections.get(server_name)
        if connection is None:
            if self.parent is not None and self.parent.status(server_name) is not None:
                return await self.parent._request(server_name, operation, reconnect=reconnect)
            raise MCPServerNotFound(f'MCP server "{server_name}" is unavailable')
        if connection.task.done():
            if not reconnect:
                raise MCPError(f'MCP server "{server_name}" connection is closed')
            config = connection.config
            await self.disconnect(server_name)
            await self.connect(config)
            connection = self._connections[server_name]
        try:
            return await connection.request(
                operation,
                timeout=connection.config.timeout,
                cancellation=self.harness.runtime_context.cancellation,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            raise
        except Exception:
            if not reconnect:
                raise
            config = connection.config
            await self.disconnect(server_name)
            await self.connect(config)
            return await self._connections[server_name].request(
                operation,
                timeout=config.timeout,
                cancellation=self.harness.runtime_context.cancellation,
            )

    def _set_record(
        self,
        config: MCPServerConfig,
        status: MCPServerStatus,
        *,
        error: str | None = None,
    ) -> MCPServerRecord:
        record = MCPServerRecord(
            name=config.name,
            transport=config.transport,
            status=status,
            config_digest=config.digest(),
            agent_id=self.harness.agent_id,
            error=error,
        )
        self._records[config.name] = record

        def mutation(snapshot: dict[str, Any]) -> dict[str, Any]:
            servers = {
                key: dict(value)
                for key, value in snapshot.get("servers", {}).items()
                if isinstance(key, str) and isinstance(value, Mapping)
            }
            servers[config.name] = {
                "status": status.value,
                "transport": config.transport.value,
                "config_digest": record.config_digest,
                "agent_id": record.agent_id,
                "error": error,
            }
            return {"servers": servers}

        self._mutate_metadata(mutation)
        return record

    def _mutate_metadata(
        self, mutation: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        repository = self.harness.store.metadata
        for _ in range(16):
            current = repository.get(self.harness.root_session_id, MCP_RUNTIME_NAMESPACE)
            snapshot = dict(current.snapshot) if current is not None else {}
            expected = current.revision if current is not None else None
            try:
                repository.put(
                    self.harness.root_session_id,
                    MCP_RUNTIME_NAMESPACE,
                    mutation(snapshot),
                    expected,
                )
                return
            except RuntimeRecordRevisionConflict:
                continue
        raise RuntimeError("MCP runtime metadata update conflicted repeatedly")


def _json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
