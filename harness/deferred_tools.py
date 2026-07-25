"""Per-harness deferred tool visibility, activation, and MCP discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from state_core import EventType, RuntimeRecordRevisionConflict
from tools.base import Tool, ToolResult, ToolSpec, canonical_tool_name, tool_flag

if TYPE_CHECKING:
    from .mcp import MCPToolDefinition
    from .session import SessionHarness


DEFERRED_TOOLS_NAMESPACE = "tools.deferred"


class DeferredToolError(RuntimeError):
    category = "deferred_tool"


class DeferredToolNotFound(DeferredToolError):
    category = "not_found"


class DeferredToolNotActive(DeferredToolError):
    category = "not_active"


class DeferredToolUnavailable(DeferredToolError):
    category = "mcp_unavailable"


@dataclass(frozen=True)
class DeferredSearchResult:
    query: str
    matches: tuple[str, ...]
    total_deferred_tools: int

    @property
    def selected(self) -> str | None:
        return self.matches[0] if self.query.lower().startswith("select:") and self.matches else None


@dataclass
class _DynamicTool:
    tool: Tool
    spec: ToolSpec
    server: str
    available: bool = True


class _MCPDeferredTool(Tool[dict[str, Any], Any]):
    input_type = dict
    should_defer = True

    def __init__(
        self,
        harness: "SessionHarness",
        canonical_name: str,
        definition: "MCPToolDefinition",
    ) -> None:
        self.harness = harness
        self.name = canonical_name
        self.description = definition.description
        self.search_hint = f"{definition.server} {definition.name} {definition.description}"
        self.server = definition.server
        self.remote_name = definition.name
        self.input_schema = dict(definition.input_schema)
        super().__init__()

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        value = await self.harness.mcp.call_tool(
            self.server, self.remote_name, input_data
        )
        return ToolResult.ok(value)

    def get_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }


class DeferredToolRegistry:
    """Separate tool availability from schemas visible to one agent scope."""

    def __init__(
        self,
        harness: "SessionHarness",
        *,
        parent: "DeferredToolRegistry | None" = None,
    ) -> None:
        self.harness = harness
        self.registry = harness.tool_runtime.registry
        self.parent = parent
        self._dynamic: dict[str, _DynamicTool] = {}

    @property
    def scope_key(self) -> str:
        return "root" if self.harness.agent_id is None else f"agent:{self.harness.agent_id}"

    def activations(self) -> frozenset[str]:
        record = self.harness.store.metadata.get(
            self.harness.root_session_id, DEFERRED_TOOLS_NAMESPACE
        )
        if record is None:
            return frozenset()
        scopes = record.snapshot.get("activations", {})
        if not isinstance(scopes, Mapping):
            return frozenset()
        values = scopes.get(self.scope_key, [])
        if not isinstance(values, list):
            return frozenset()
        return frozenset(value for value in values if isinstance(value, str))

    def activate(self, name: str) -> str:
        canonical = self.resolve_name(name)
        if canonical is None:
            raise DeferredToolNotFound(f"Tool {name!r} is not available")
        tool = self.resolve_tool(canonical)
        if tool is not None and not tool_flag(tool, "should_defer"):
            return canonical
        if canonical in self.activations():
            return canonical

        def mutation(snapshot: dict[str, Any]) -> dict[str, Any]:
            scopes = {
                key: list(value)
                for key, value in snapshot.get("activations", {}).items()
                if isinstance(key, str) and isinstance(value, list)
            }
            values = {
                value for value in scopes.get(self.scope_key, []) if isinstance(value, str)
            }
            values.add(canonical)
            scopes[self.scope_key] = sorted(values)
            return {**snapshot, "activations": scopes}

        self._mutate_metadata(mutation)
        self.harness.session_runtime.append_event(
            EventType.TOOL_ACTIVATED,
            {"tool": canonical, "agentId": self.harness.agent_id},
        )
        return canonical

    def require_active(self, name: str, agent_id: str | None = None) -> None:
        if agent_id != self.harness.agent_id:
            raise DeferredToolError("deferred tool scope does not match the active harness")
        canonical = self.resolve_name(name) or name
        tool = self.resolve_tool(canonical)
        if tool is not None and not tool_flag(tool, "should_defer"):
            return
        if canonical not in self.activations():
            raise DeferredToolNotActive(
                f"Tool {canonical!r} is deferred. Load it first with "
                f'ToolSearch query "select:{canonical}".'
            )
        dynamic = self._dynamic_entry(canonical)
        if dynamic is not None and not dynamic.available:
            raise DeferredToolUnavailable(
                f'MCP tool {canonical!r} is unavailable because server "{dynamic.server}" is disconnected'
            )
        if tool is None:
            if canonical.startswith("mcp__"):
                raise DeferredToolUnavailable(
                    f"MCP tool {canonical!r} is unavailable until its server reconnects"
                )
            raise DeferredToolNotFound(f"Tool {canonical!r} is not available")

    def resolve_name(self, name: str) -> str | None:
        canonical = self.registry.resolve_name(name)
        if canonical is not None:
            return canonical
        lowered = name.lower()
        for candidate in self._all_dynamic():
            if candidate.lower() == lowered:
                return candidate
        normalized = canonical_tool_name(name)
        for candidate in self._all_dynamic():
            if canonical_tool_name(candidate) == normalized:
                return candidate
        if name in self.activations() and name.startswith("mcp__"):
            return name
        return None

    def resolve_tool(self, name: str) -> Tool | None:
        static = self.registry.get(name)
        if static is not None:
            return static
        dynamic = self._dynamic_entry(name)
        return dynamic.tool if dynamic is not None else None

    def get_spec(self, name: str) -> ToolSpec | None:
        static = self.registry.get_spec(name)
        if static is not None:
            return static
        dynamic = self._dynamic_entry(name)
        return dynamic.spec if dynamic is not None else None

    def visible_names(self) -> tuple[str, ...]:
        active = self.activations()
        names: list[str] = []
        for name in self.registry.list_tools():
            tool = self.registry.get(name)
            if tool is None or not self._enabled(tool):
                continue
            if not tool_flag(tool, "should_defer") or name in active:
                names.append(name)
        for name, dynamic in self._all_dynamic().items():
            if dynamic.available and name in active:
                names.append(name)
        return tuple(dict.fromkeys(names))

    def visible_specs(self) -> tuple[ToolSpec, ...]:
        return tuple(
            spec
            for name in self.visible_names()
            if (spec := self.get_spec(name)) is not None
        )

    def visible_schemas(self) -> list[dict[str, Any]]:
        return [spec.to_openai() for spec in self.visible_specs()]

    def search(self, query: str, *, max_results: int = 5) -> DeferredSearchResult:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty")
        if isinstance(max_results, bool) or not isinstance(max_results, int) or max_results <= 0:
            raise ValueError("max_results must be a positive integer")
        candidates = self._deferred_candidates()
        select = re.fullmatch(r"select:(.+)", query.strip(), flags=re.IGNORECASE)
        if select is not None:
            found: list[str] = []
            for requested in (item.strip() for item in select.group(1).split(",")):
                if not requested:
                    continue
                canonical = self.resolve_name(requested)
                if canonical is None:
                    continue
                if canonical not in found:
                    self.activate(canonical)
                    found.append(canonical)
            return DeferredSearchResult(query, tuple(found), len(candidates))

        lowered = query.strip().lower()
        exact = self.resolve_name(query.strip())
        if exact is not None:
            return DeferredSearchResult(query, (exact,), len(candidates))
        terms = [term.lstrip("+") for term in lowered.split() if term.lstrip("+")]
        required = [term[1:] for term in lowered.split() if term.startswith("+") and len(term) > 1]
        scored: list[tuple[int, str]] = []
        for name, tool in candidates.items():
            description = str(getattr(tool, "description", "")).lower()
            hint = str(getattr(tool, "search_hint", "")).lower()
            searchable = f"{name.lower().replace('_', ' ')} {description} {hint}"
            if required and not all(term in searchable for term in required):
                continue
            score = sum(
                (10 if term in name.lower() else 0)
                + (4 if term in hint else 0)
                + (2 if term in description else 0)
                for term in terms
            )
            if score:
                scored.append((score, name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return DeferredSearchResult(
            query,
            tuple(name for _, name in scored[:max_results]),
            len(candidates),
        )

    def register_mcp_tools(
        self, server: str, definitions: Iterable["MCPToolDefinition"]
    ) -> tuple[str, ...]:
        current: set[str] = set()
        for definition in definitions:
            canonical = self._mcp_name(server, definition.name)
            tool = _MCPDeferredTool(self.harness, canonical, definition)
            self._dynamic[canonical] = _DynamicTool(
                tool=tool,
                spec=ToolSpec.from_tool(tool, name=canonical),
                server=server,
                available=True,
            )
            current.add(canonical)
        for name, entry in self._dynamic.items():
            if entry.server == server and name not in current:
                entry.available = False
        return tuple(sorted(current))

    def set_mcp_server_available(self, server: str, available: bool) -> None:
        for entry in self._dynamic.values():
            if entry.server == server:
                entry.available = available

    def _deferred_candidates(self) -> dict[str, Tool]:
        candidates = {
            name: tool
            for name in self.registry.list_tools()
            if (tool := self.registry.get(name)) is not None
            and self._enabled(tool)
            and tool_flag(tool, "should_defer")
        }
        candidates.update(
            {name: entry.tool for name, entry in self._all_dynamic().items() if entry.available}
        )
        return candidates

    def _enabled(self, tool: Tool) -> bool:
        enabled = getattr(tool, "is_enabled", True)
        if callable(enabled):
            context = self.harness.tool_runtime.tool_context(self.harness.runtime_context)
            try:
                return bool(enabled(context))
            except TypeError:
                return bool(enabled())
        return bool(enabled)

    def _all_dynamic(self) -> dict[str, _DynamicTool]:
        inherited = self.parent._all_dynamic() if self.parent is not None else {}
        return {**inherited, **self._dynamic}

    def _dynamic_entry(self, name: str) -> _DynamicTool | None:
        entry = self._dynamic.get(name)
        if entry is not None:
            return entry
        return self.parent._dynamic_entry(name) if self.parent is not None else None

    def _mutate_metadata(self, mutation) -> None:
        repository = self.harness.store.metadata
        for _ in range(16):
            current = repository.get(
                self.harness.root_session_id, DEFERRED_TOOLS_NAMESPACE
            )
            snapshot = dict(current.snapshot) if current is not None else {}
            expected = current.revision if current is not None else None
            try:
                repository.put(
                    self.harness.root_session_id,
                    DEFERRED_TOOLS_NAMESPACE,
                    mutation(snapshot),
                    expected,
                )
                return
            except RuntimeRecordRevisionConflict:
                continue
        raise RuntimeError("deferred tool activation conflicted repeatedly")

    @staticmethod
    def _mcp_name(server: str, remote_name: str) -> str:
        safe_server = re.sub(r"[^A-Za-z0-9_-]+", "_", server).strip("_")
        safe_tool = re.sub(r"[^A-Za-z0-9_-]+", "_", remote_name).strip("_")
        if not safe_server or not safe_tool:
            raise ValueError("MCP server and tool names must contain safe characters")
        return f"mcp__{safe_server}__{safe_tool}"
