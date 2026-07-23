# P0 Harness Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one reliable tool and agent execution path with normalized contracts, centralized permissions, cancellation, timeouts, and controllable subagents.

**Architecture:** Add a small `harness` package containing immutable runtime context, permission policy, cancellation, and tool execution. Extend the existing tool registry with normalization and aliases, then migrate `QueryEngine` and `AgentExecutor` to the shared runtime. Keep the worker-pool implementation importable but remove its duplicate tool from the primary catalog.

**Tech Stack:** Python 3.10+, asyncio, dataclasses, pathlib, standard-library unittest, existing FastAPI/Pydantic services.

---

### Task 1: Lock the tool contract

**Files:**
- Create: `tests/test_tool_contract.py`
- Modify: `tools/base.py`

- [ ] Write failing tests asserting `ToolResult.ok(...).error is None`, `ToolResult.fail(...)` preserves an error, every registered tool exposes an object parameter schema, and Claude-style aliases resolve to canonical tools.
- [ ] Run `.venv/bin/python -m unittest tests.test_tool_contract -v` and verify failures identify the field/method collision and missing schema normalization.
- [ ] Add `ToolSpec`, schema normalization, canonical alias resolution, and `ToolResult.fail()`; migrate `ToolResult.error()` and `ToolResult.success()` call sites mechanically.
- [ ] Re-run the contract tests and verify all registered tools have usable schemas.

### Task 2: Add runtime permissions and cancellation

**Files:**
- Create: `harness/__init__.py`
- Create: `harness/context.py`
- Create: `harness/permissions.py`
- Create: `harness/runtime.py`
- Create: `tests/test_tool_runtime.py`

- [ ] Write failing tests for allowed read-only calls, destructive-call approval, fail-closed missing approval, workspace path escape denial, timeout, and cancellation.
- [ ] Run `.venv/bin/python -m unittest tests.test_tool_runtime -v` and verify the runtime imports or behaviors fail.
- [ ] Implement `RuntimeContext`, `CancellationToken`, `PermissionDecision`, `PermissionPolicy`, `ToolExecution`, and `ToolRuntime.execute()`.
- [ ] Re-run the runtime tests and refactor only after they pass.

### Task 3: Migrate QueryEngine

**Files:**
- Create: `tests/test_query_engine_runtime.py`
- Modify: `query_engine.py`

- [ ] Write failing tests proving tool schemas use normalized names/schema, Plan mode retains canonical read tools, mutating tool calls are sequential, and request temperature reaches the non-streaming LLM request.
- [ ] Run `.venv/bin/python -m unittest tests.test_query_engine_runtime -v` and verify expected failures.
- [ ] Replace direct registry execution with `ToolRuntime`, canonicalize the Plan allowlist, pass runtime context, and propagate the request temperature.
- [ ] Re-run QueryEngine tests and the tool runtime tests.

### Task 4: Migrate and stabilize subagents

**Files:**
- Create: `tests/test_agent_runtime.py`
- Modify: `agents/types.py`
- Modify: `agents/built_in.py`
- Modify: `agents/engine.py`
- Modify: `agents/tool.py`
- Modify: `tools/__init__.py`

- [ ] Write failing tests proving built-in agents resolve tools, assistant tool calls are retained in LLM history, foreground spawn actually runs, background wait returns a result, and abort cancels the tracked task.
- [ ] Run `.venv/bin/python -m unittest tests.test_agent_runtime -v` and verify the current lifecycle fails.
- [ ] Migrate built-in tool names, execute tools through `ToolRuntime`, preserve assistant `tool_calls`, store background tasks/futures, and implement wait/abort/cleanup.
- [ ] Make the root `Agent` implementation the only Agent tool imported into the primary registry while preserving worker-pool imports for direct callers.
- [ ] Re-run agent and contract tests.

### Task 5: Verify the P0 boundary

**Files:**
- Modify only files required by failures found in the previous tasks.

- [ ] Run `.venv/bin/python -m unittest discover -s tests -v`.
- [ ] Run a registry audit confirming zero tools have missing parameter schemas and no duplicate canonical names exist.
- [ ] Run import/compile checks for `main`, `query_engine`, `agents`, `tools`, and `harness`.
- [ ] Inspect `git diff` to confirm unrelated user changes are preserved and document any optional test dependency preventing legacy pytest tests from running.
