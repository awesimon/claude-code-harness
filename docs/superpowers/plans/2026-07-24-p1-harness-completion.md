# P1 Durable Harness Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the durable Python agent harness defined in `docs/superpowers/specs/2026-07-24-p1-harness-completion-design.md` while preserving public HTTP and tool contracts.

**Architecture:** A session-scoped `SessionHarness` composes durable state, agent scheduling, the tool pipeline, hooks, skills, context control, budgets, traces, MCP, worktrees, and deferred tools. Each subsystem writes through state-core; QueryEngine and compatibility APIs delegate to the harness instead of maintaining competing managers.

**Tech Stack:** Python 3.10+, asyncio, SQLAlchemy 2, Pydantic 2, FastAPI, official Python MCP SDK, pytest, pytest-asyncio, temporary Git repositories.

---

## File Structure

New focused modules:

- `state_core/runtime_records.py`: immutable domain records and repository protocols for agents, runtime metadata, traces, and worktrees.
- `state_core/sqlalchemy_runtime.py`: SQLAlchemy tables and repositories for those records.
- `harness/session.py`: `SessionHarness`, child scopes, and `SessionHarnessFactory`.
- `harness/agents.py`: durable `AgentScheduler` and live asyncio task ownership.
- `harness/hooks.py`: hook definitions, matching, subprocess execution, and hook outputs.
- `harness/skills.py`: progressive skill index, safe resolution, and snapshots.
- `harness/budget.py`: hierarchical reservations and durable usage accounting.
- `harness/tracing.py`: durable span lifecycle and summaries.
- `harness/context_control.py`: micro-compaction, durable compact boundaries, and restore.
- `harness/mcp.py`: real MCP connection and discovery lifecycle.
- `harness/worktrees.py`: explicit-cwd worktree ownership and cleanup.
- `harness/deferred_tools.py`: per-agent deferred schema visibility and activation.
- `tools/agent_runtime_tools.py`: canonical Agent, TaskOutput, and TaskStop-compatible tools.

Existing modules rewritten or reduced to adapters:

- `harness/runtime.py`: ordered tool pipeline only.
- `agents/engine.py`: one subagent execution loop; no global lifecycle registry.
- `agents/tool.py`: compatibility re-export of canonical Agent tool.
- `query_engine.py`: harness-driven query loop and context control.
- `tools/hooks_tools.py`, `tools/skill_tool_v2.py`, `tools/mcp_*.py`, `tools/worktree_tool.py`, `tools/tool_search_tool.py`: public tool adapters over harness services.
- `services/conversation_service.py`, `services/task_service.py`, `services/plan_service.py`, `routers/plan.py`, `routers/agents.py`: final HTTP compatibility adapters.

## Stage 1: Durable Harness And Agent Scheduler

### Task 1: Runtime Record Repositories

**Files:**
- Create: `state_core/runtime_records.py`
- Create: `state_core/sqlalchemy_runtime.py`
- Modify: `state_core/sqlalchemy_store.py`
- Modify: `state_core/__init__.py`
- Test: `tests/state_core/test_runtime_records.py`

- [ ] **Step 1: Write failing agent lifecycle repository tests**

```python
def test_agent_transition_is_durable(runtime_store):
    created = runtime_store.agents.create(
        AgentRecord.new("a1", root_session_id="s1", agent_type="Explore", prompt="inspect")
    )
    running = runtime_store.agents.transition("a1", created.revision, AgentStatus.RUNNING)
    completed = runtime_store.agents.transition(
        "a1", running.revision, AgentStatus.COMPLETED,
        termination_reason=TerminationReason.COMPLETED,
    )
    assert runtime_store.agents.get("a1") == completed

def test_terminal_agent_cannot_restart(runtime_store):
    record = completed_agent(runtime_store, "a1")
    with pytest.raises(InvalidAgentTransition):
        runtime_store.agents.transition("a1", record.revision, AgentStatus.RUNNING)
```

- [ ] **Step 2: Run the tests and verify the missing record API fails**

Run: `.venv/bin/pytest tests/state_core/test_runtime_records.py -q`

Expected: collection fails because `AgentRecord` and runtime repositories do not exist.

- [ ] **Step 3: Define stable records and protocols**

```python
class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    ORPHANED = "orphaned"

@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    root_session_id: str
    parent_agent_id: str | None
    agent_type: str
    prompt: str
    description: str
    is_background: bool
    status: AgentStatus
    revision: int
    definition: dict[str, Any]
    usage: dict[str, float]
    termination_reason: str | None
    error: dict[str, Any] | None
    output: dict[str, Any] | None
    effective_cwd: str | None
    worktree_id: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime
```

Also define `RuntimeMetadataRecord`, `TraceSpanRecord`, `WorktreeRecord`, and storage-neutral repository protocols with revision-checked writes.

- [ ] **Step 4: Implement SQLAlchemy tables and repositories**

Use dedicated `runtime_agents`, `runtime_metadata`, `runtime_trace_spans`, and `runtime_worktrees` tables. Store JSON only after `to_json_value`-equivalent domain validation. Add repositories to `SQLAlchemyStateStore` as `agents`, `metadata`, `traces`, and `worktrees`.

- [ ] **Step 5: Add transition, revision conflict, list, and reconciliation tests**

Cover parent/root filtering, terminal transition rejection, stale revision rejection, metadata snapshots, open-span closure, and `pending`/`running` reconciliation to `interrupted`.

- [ ] **Step 6: Run focused and state-core tests**

Run: `.venv/bin/pytest tests/state_core/test_runtime_records.py tests/state_core -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add state_core tests/state_core/test_runtime_records.py
git commit -m "feat: persist harness runtime records"
```

### Task 2: SessionHarness Composition Root

**Files:**
- Create: `harness/session.py`
- Modify: `harness/context.py`
- Modify: `harness/__init__.py`
- Test: `tests/test_session_harness.py`

- [ ] **Step 1: Write failing root and child scope tests**

```python
def test_child_scope_inherits_root_and_isolates_agent_state(harness_factory):
    root = harness_factory.create("s1")
    child = root.child("a1")
    assert child.session_runtime is root.session_runtime
    assert child.agent_id == "a1"
    assert child.cancellation is not root.cancellation
    assert child.cancellation.parent is root.cancellation
    assert child.effective_cwd == root.effective_cwd
```

- [ ] **Step 2: Verify the test fails because SessionHarness is absent**

Run: `.venv/bin/pytest tests/test_session_harness.py -q`

- [ ] **Step 3: Implement the composition API**

```python
@dataclass
class SessionHarness:
    session_runtime: SessionRuntime
    tool_runtime: ToolRuntime
    cancellation: CancellationToken
    effective_cwd: Path
    agent_id: str | None = None
    parent_agent_id: str | None = None

    def child(self, agent_id: str, *, cwd: Path | None = None) -> "SessionHarness":
        return replace(
            self,
            agent_id=agent_id,
            cancellation=CancellationToken(parent=self.cancellation),
            effective_cwd=(cwd or self.effective_cwd).resolve(),
        )

class SessionHarnessFactory:
    def create(self, session_id: str) -> SessionHarness:
        runtime = self.runtime_factory.create(session_id)
        return self._compose(runtime)

    def resume(self, session_id: str) -> SessionHarness:
        runtime = self.runtime_factory.resume(session_id)
        harness = self._compose(runtime)
        harness.agent_scheduler.reconcile()
        return harness
```

The factory owns process-local caches only for live harness objects and transport handles. Durable identity always loads from `SessionRuntimeFactory`.

- [ ] **Step 4: Test resume, cancellation propagation, explicit cwd, and Todo agent scope**

Run: `.venv/bin/pytest tests/test_session_harness.py tests/state_core/test_recovery.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add harness tests/test_session_harness.py
git commit -m "feat: add session harness composition root"
```

### Task 3: Durable Agent Scheduler

**Files:**
- Create: `harness/agents.py`
- Rewrite: `agents/engine.py`
- Modify: `agents/types.py`
- Modify: `agents/__init__.py`
- Test: `tests/test_agent_scheduler.py`
- Modify: `tests/test_agent_runtime.py`

- [ ] **Step 1: Write failing foreground/background scheduler tests**

```python
@pytest.mark.asyncio
async def test_background_spawn_persists_before_return(harness, fake_executor):
    scheduler = AgentScheduler(harness, executor_factory=fake_executor)
    launched = await scheduler.spawn(AgentRequest(prompt="inspect", agent_type="Explore", background=True))
    assert launched.status is AgentStatus.RUNNING
    assert harness.store.agents.get(launched.agent_id).status is AgentStatus.RUNNING
    result = await scheduler.wait(launched.agent_id)
    assert result.status is AgentStatus.COMPLETED

@pytest.mark.asyncio
async def test_resume_marks_unowned_running_agent_interrupted(harness_factory, runtime_store):
    runtime_store.agents.create(running_agent("a1", "s1"))
    resumed = harness_factory.resume("s1")
    assert resumed.agent_scheduler.status("a1").status is AgentStatus.INTERRUPTED
```

- [ ] **Step 2: Run and verify scheduler tests fail**

Run: `.venv/bin/pytest tests/test_agent_scheduler.py -q`

- [ ] **Step 3: Implement scheduler operations**

```python
class AgentScheduler:
    async def spawn(self, request: AgentRequest) -> AgentRecord:
        record = self.repository.create(AgentRecord.from_request(self.harness, request))
        task = asyncio.create_task(self._run(record))
        self._live_tasks[record.agent_id] = task
        return record if request.background else await self.wait(record.agent_id)

    async def wait(self, agent_id: str, timeout: float | None = None) -> AgentRecord:
        task = self._live_tasks.get(agent_id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        record = self.repository.get(agent_id)
        if record is None:
            raise AgentNotFound(agent_id)
        return record

    def status(self, agent_id: str) -> AgentRecord | None:
        return self.repository.get(agent_id)

    def list(self, *, parent_agent_id: str | None = None) -> list[AgentRecord]:
        return self.repository.list(self.harness.session_id, parent_agent_id=parent_agent_id)

    async def stop(self, agent_id: str, grace_seconds: float = 1.0) -> AgentRecord:
        self._cancellations[agent_id].cancel()
        return await self._finish_stop(agent_id, grace_seconds)

    def reconcile(self) -> list[AgentRecord]:
        return self.repository.interrupt_unowned(self.harness.session_id, set(self._live_tasks))
```

Use an asyncio semaphore for root concurrency and a child semaphore keyed by parent agent. Persist `pending` before queueing, `running` before calling the executor, and terminal output before releasing waiters.

- [ ] **Step 4: Rewrite AgentExecutor as one execution loop**

Remove lifecycle dictionaries and global manager ownership from `agents/engine.py`. Keep definition/tool filtering and model loop behavior. Accept a child `SessionHarness`; return a terminal `AgentExecutionResult` without persisting lifecycle itself.

- [ ] **Step 5: Test cancellation races, timeout, nested parent IDs, limits, and failures**

Run: `.venv/bin/pytest tests/test_agent_scheduler.py tests/test_agent_runtime.py -q`

Expected: PASS with no pending asyncio task warnings.

- [ ] **Step 6: Commit**

```bash
git add harness/agents.py agents tests/test_agent_scheduler.py tests/test_agent_runtime.py
git commit -m "feat: add durable agent scheduler"
```

### Task 4: Canonical Agent Runtime Tools And QueryEngine Integration

**Files:**
- Create: `tools/agent_runtime_tools.py`
- Rewrite: `agents/tool.py`
- Modify: `tools/__init__.py`
- Modify: `query_engine.py`
- Test: `tests/test_agent_runtime_tools.py`
- Modify: `tests/state_core/test_query_engine_state.py`

- [ ] **Step 1: Write failing tool contract tests**

```python
@pytest.mark.asyncio
async def test_agent_tool_uses_active_harness(tool_context):
    result = await AgentTool().run({
        "prompt": "inspect", "description": "Inspect", "subagent_type": "Explore",
        "run_in_background": True,
    }, tool_context)
    assert result.data["status"] == "async_launched"
    assert result.data["agent_id"]

@pytest.mark.asyncio
async def test_task_output_and_stop_share_scheduler(tool_context):
    launched = await launch_background(tool_context)
    status = await TaskOutputTool().run({"task_id": launched, "block": False}, tool_context)
    stopped = await TaskStopTool().run({"task_id": launched}, tool_context)
    assert status.data["status"] in {"pending", "running"}
    assert stopped.data["status"] == "cancelled"
```

- [ ] **Step 2: Verify failure against the current foreground-only Agent tool**

Run: `.venv/bin/pytest tests/test_agent_runtime_tools.py -q`

- [ ] **Step 3: Implement compatible tool schemas and adapters**

Agent accepts existing `prompt` and `subagent_type`, plus compatible `description`, `run_in_background`, `model`, `cwd`, and `isolation`. TaskOutput accepts task/agent ID, blocking mode, and timeout. TaskStop accepts task/agent ID. Legacy Agent list/destroy aliases delegate to scheduler list/stop.

- [ ] **Step 4: Replace QueryEngine runtime dictionaries with SessionHarnessFactory**

`QueryEngine._session_harness(conversation_id)` becomes the only session runtime lookup. Its public `spawn_agent`, `get_agent_status`, and `abort_agent` methods delegate to the harness scheduler.

- [ ] **Step 5: Run Agent, QueryEngine, and state-core regression suites**

Run: `.venv/bin/pytest tests/test_agent_runtime_tools.py tests/test_agent_scheduler.py tests/test_agent_runtime.py tests/state_core/test_query_engine_state.py -q`

Expected: PASS.

- [ ] **Step 6: Run Stage 1 gate and commit**

Run: `.venv/bin/pytest tests/state_core tests/test_session_harness.py tests/test_agent_scheduler.py tests/test_agent_runtime.py tests/test_agent_runtime_tools.py tests/test_query_engine_runtime.py -q`

```bash
git add tools/agent_runtime_tools.py tools/__init__.py agents/tool.py query_engine.py tests
git commit -m "feat: route agents through session harness"
```

## Stage 2: Tool Pipeline, Hooks, And Skills

### Task 5: Executable Hook Runtime

**Files:**
- Create: `harness/hooks.py`
- Rewrite: `tools/hooks_tools.py`
- Modify: `harness/session.py`
- Test: `tests/test_hook_runtime.py`

- [ ] **Step 1: Write failing hook behavior tests**

```python
@pytest.mark.asyncio
async def test_pre_tool_hook_updates_input(tmp_path):
    hook = command_hook("PreToolUse", script_that_returns({"decision": "allow", "updated_input": {"path": "b.txt"}}))
    result = await HookRuntime([hook]).run_pre_tool("read_file", {"path": "a.txt"}, hook_context())
    assert result.input == {"path": "b.txt"}

@pytest.mark.asyncio
async def test_pre_tool_hook_blocks_and_post_hook_fails_open(blocking_hook, failing_post_hook):
    runtime = HookRuntime([blocking_hook, failing_post_hook])
    blocked = await runtime.run_pre_tool("bash", {"command": "false"}, hook_context())
    assert blocked.decision is HookDecision.BLOCK
    observed = await runtime.run_post_tool(
        "read_file", {"path": "a.txt"}, {"content": "ok"}, hook_context()
    )
    assert observed.result == {"content": "ok"}
    assert observed.failures[0].hook_id == failing_post_hook.hook_id
```

- [ ] **Step 2: Verify tests fail because hooks only edit config**

Run: `.venv/bin/pytest tests/test_hook_runtime.py -q`

- [ ] **Step 3: Implement HookDefinition, HookDecision, matching, and command runner**

Command hooks receive JSON stdin, an environment allowlist, explicit cwd, timeout, cancellation, and output-size limit. Parse stdout as one JSON object. Make Pre mutation gates fail closed and observational Post hooks fail open by default.

- [ ] **Step 4: Rewrite hook tools as configuration adapters**

Keep current public list/add/remove/events shapes. Store normalized hook configuration in session metadata; user/project config import occurs once when the harness snapshot is created.

- [ ] **Step 5: Test timeout, malformed output, cancellation, matchers, recursion guard, and durable events**

Run: `.venv/bin/pytest tests/test_hook_runtime.py -q`

- [ ] **Step 6: Commit**

```bash
git add harness/hooks.py harness/session.py tools/hooks_tools.py tests/test_hook_runtime.py
git commit -m "feat: execute session hooks"
```

### Task 6: Progressive Skill Resolver

**Files:**
- Create: `harness/skills.py`
- Modify: `services/skill_loader.py`
- Rewrite: `services/skill_registry.py`
- Rewrite: `tools/skill_tool_v2.py`
- Modify: `agents/engine.py`
- Test: `tests/test_skill_runtime.py`

- [ ] **Step 1: Write failing discovery and snapshot tests**

```python
def test_skill_body_is_loaded_only_when_selected(skill_dir, harness):
    resolver = SkillResolver(skill_dir, harness.metadata)
    assert resolver.index()[0].content is None
    selected = resolver.resolve("reviewing")
    assert "Review carefully" in selected.content
    assert harness.metadata.skill_snapshot("reviewing").digest == selected.digest

def test_skill_reference_cannot_escape_base_directory(skill_dir):
    with pytest.raises(SkillPathError):
        SkillResolver(skill_dir).read_resource("reviewing", "../secret")
```

- [ ] **Step 2: Verify tests fail against eager global skill loading**

Run: `.venv/bin/pytest tests/test_skill_runtime.py -q`

- [ ] **Step 3: Implement index, digest, resolve, snapshot, and containment**

Index entries contain name, description, canonical base path, digest, and metadata only. Resolved snapshots include content, allowed tools, hooks, MCP requirements, and referenced resource manifest.

- [ ] **Step 4: Integrate skills into child prompt and tool resolution**

Resolve Agent definition skills before the first child model call. Append the snapshot content to the child system prompt, intersect allowed tools with Agent policy, register skill hooks, and initialize required MCP servers.

- [ ] **Step 5: Remove unrestricted custom Python execution**

Delete or replace any dynamic `exec` path. Skill scripts return a normal Bash/subprocess tool request and therefore cross permission, hook, timeout, budget, cwd, cancellation, and transcript boundaries.

- [ ] **Step 6: Test changed source on resume, tool isolation, hook registration, and script routing**

Run: `.venv/bin/pytest tests/test_skill_runtime.py tests/test_skill_loader.py tests/test_agent_runtime.py -q`

- [ ] **Step 7: Commit**

```bash
git add harness/skills.py services/skill_loader.py services/skill_registry.py tools/skill_tool_v2.py agents/engine.py tests
git commit -m "feat: add progressive agent skills"
```

### Task 7: Ordered Tool Pipeline

**Files:**
- Rewrite: `harness/runtime.py`
- Modify: `harness/session.py`
- Modify: `tools/base.py`
- Test: `tests/test_tool_pipeline.py`
- Modify: `tests/test_tool_runtime.py`

- [ ] **Step 1: Write a failing order test**

```python
@pytest.mark.asyncio
async def test_pipeline_order(pipeline, recorder):
    await pipeline.execute("sample", {"value": 1}, runtime_context(recorder))
    assert recorder.calls == [
        "deferred", "validate", "pre_hook", "permission", "reserve",
        "execute", "normalize", "post_hook", "consume", "persist",
    ]
```

- [ ] **Step 2: Verify the current runtime skips deferred/hooks/budget/persistence**

Run: `.venv/bin/pytest tests/test_tool_pipeline.py -q`

- [ ] **Step 3: Implement the pipeline with one terminal result**

Extend termination reasons to `timed_out`, `budget_exhausted`, `hook_blocked`, `mcp_unavailable`, `interrupted`, and `orphaned`. Ensure validation, permission denial, hook block, timeout, cancellation, exceptions, and JSON normalization each return one durable result.

- [ ] **Step 4: Preserve legacy Tool.run compatibility through contextvars**

Tool implementations may retain `execute(input)` but receive the active harness and runtime context via the existing contextvar. Remove tool-specific calls that bypass the pipeline.

- [ ] **Step 5: Test every terminal path and exact transcript pairing**

Run: `.venv/bin/pytest tests/test_tool_pipeline.py tests/test_tool_runtime.py tests/test_tool_contract.py tests/state_core/test_query_engine_state.py -q`

- [ ] **Step 6: Run Stage 2 gate and commit**

Run: `.venv/bin/pytest tests/test_hook_runtime.py tests/test_skill_runtime.py tests/test_tool_pipeline.py tests/test_agent_runtime_tools.py -q`

```bash
git add harness tools/base.py tests
git commit -m "feat: enforce controlled tool pipeline"
```

## Stage 3: Context, Budgets, And Observability

### Task 8: Hierarchical Budgets And Durable Traces

**Files:**
- Create: `harness/budget.py`
- Create: `harness/tracing.py`
- Modify: `harness/session.py`
- Modify: `harness/runtime.py`
- Modify: `agents/engine.py`
- Test: `tests/test_budget_runtime.py`
- Test: `tests/test_tracing_runtime.py`

- [ ] **Step 1: Write failing reservation and trace tests**

```python
def test_concurrent_reservations_cannot_exceed_root_budget(budget):
    first = budget.reserve(BudgetKind.TOOL_CALLS, 1, agent_id="a1")
    with pytest.raises(BudgetExhausted):
        budget.reserve(BudgetKind.TOOL_CALLS, 1, agent_id="a2")
    first.release()

def test_open_span_is_closed_as_interrupted_on_resume(trace_repo):
    opened = trace_repo.start(TraceSpanRecord.new("span-1", "s1", "tool", "bash"))
    assert opened.status is SpanStatus.RUNNING
    closed = trace_repo.interrupt_open("s1")
    assert [span.span_id for span in closed] == ["span-1"]
    assert trace_repo.get("span-1").status is SpanStatus.INTERRUPTED
```

- [ ] **Step 2: Verify missing budget and tracing APIs fail**

Run: `.venv/bin/pytest tests/test_budget_runtime.py tests/test_tracing_runtime.py -q`

- [ ] **Step 3: Implement budget limits, reservations, reconciliation, and roll-up**

Support input/output/total tokens, cost, turns, tool calls, wall clock, and compaction tokens. Child usage consumes both child and root limits atomically. Persist limits, reservations, actual consumption, and exhaustion events.

- [ ] **Step 4: Implement span context manager and durable summaries**

```python
async with traces.span("tool", "read_file", agent_id=agent_id) as span:
    result = await operation()
    span.set_usage(result.usage)
```

Sanitize recorded errors and never store credentials, raw headers, or hook environments.

- [ ] **Step 5: Integrate model, tool, agent, hook, and MCP reservation points**

Root exhaustion cancels child scopes. Child exhaustion terminates only that child. Cleanup errors are recorded but do not overwrite the original termination reason.

- [ ] **Step 6: Run focused tests and commit**

Run: `.venv/bin/pytest tests/test_budget_runtime.py tests/test_tracing_runtime.py tests/test_tool_pipeline.py tests/test_agent_scheduler.py -q`

```bash
git add harness agents/engine.py tests
git commit -m "feat: add durable budgets and traces"
```

### Task 9: Query Context Control And Durable Compaction

**Files:**
- Create: `harness/context_control.py`
- Modify: `services/compact/context_compactor.py`
- Modify: `query_engine.py`
- Modify: `state_core/types.py`
- Test: `tests/test_context_controller.py`
- Modify: `tests/test_context_compaction.py`
- Modify: `tests/state_core/test_recovery.py`

- [ ] **Step 1: Write failing automatic compaction and recovery tests**

```python
@pytest.mark.asyncio
async def test_query_engine_compacts_before_model_call(engine, oversized_history):
    await engine.query("continue", conversation_id="s1")
    assert engine.harness("s1").session_runtime.state.compact_boundary is not None

def test_resume_rebuilds_from_latest_valid_compact_boundary(runtime_factory):
    runtime = runtime_factory.create("s1")
    runtime.append_event(EventType.USER_MESSAGE, {"content": "old"})
    runtime.save_compact_boundary(summary="summary", through_event_id=runtime.state.last_event_id)
    runtime.append_event(EventType.USER_MESSAGE, {"content": "new"})
    resumed = runtime_factory.resume("s1")
    messages = ContextController(resumed).restore_messages()
    assert [message.content for message in messages] == ["summary", "new"]
```

- [ ] **Step 2: Verify QueryEngine does not invoke compaction**

Run: `.venv/bin/pytest tests/test_context_controller.py -q`

- [ ] **Step 3: Implement ContextController**

Micro-compact projected model messages without altering raw events. At the hard threshold, run PreCompact, reserve compaction budget, create a summary, atomically persist boundary plus summary, run PostCompact, and return rebuilt messages.

- [ ] **Step 4: Integrate both streaming and non-streaming QueryEngine loops**

Use the same controller before every model call. Persist actual model usage into budgets and spans. A failed compaction keeps the last valid context and returns a classified recovery result.

- [ ] **Step 5: Test hook ordering, budget exhaustion, invalid boundary fallback, and raw transcript retention**

Run: `.venv/bin/pytest tests/test_context_controller.py tests/test_context_compaction.py tests/state_core/test_recovery.py tests/state_core/test_query_engine_state.py -q`

- [ ] **Step 6: Run Stage 3 gate and commit**

```bash
git add harness/context_control.py services/compact query_engine.py state_core/types.py tests
git commit -m "feat: integrate durable context control"
```

## Stage 4: Real MCP Lifecycle

### Task 10: MCP Connection Manager And Tools

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `harness/mcp.py`
- Rewrite: `tools/mcp_tool.py`
- Rewrite: `tools/mcp_resource_tool.py`
- Rewrite: `tools/mcp_auth_tool.py`
- Modify: `harness/session.py`
- Test: `tests/mcp_test_server.py`
- Test: `tests/test_mcp_runtime.py`

- [ ] **Step 1: Add the official MCP SDK dependency**

Add `mcp>=1.9,<2` to project dependencies and run `uv sync` so stdio and streamable HTTP use the maintained protocol implementation.

- [ ] **Step 2: Write failing real-server tests**

```python
@pytest.mark.asyncio
async def test_stdio_server_discovery_call_and_resource(mcp_manager, stdio_server_config):
    await mcp_manager.connect(stdio_server_config)
    tools = await mcp_manager.list_tools("test")
    assert tools[0].name == "echo"
    assert await mcp_manager.call_tool("test", "echo", {"text": "ok"}) == {"text": "ok"}
    assert (await mcp_manager.read_resource("test", "test://value")).text == "value"
```

- [ ] **Step 3: Verify tests fail against mock results**

Run: `.venv/bin/pytest tests/test_mcp_runtime.py -q`

- [ ] **Step 4: Implement scoped connections and discovery**

`MCPConnectionManager` validates configs, lazily connects, registers discovered tools in the deferred registry, forwards structured content and metadata, reconnects transient failures once within budget, and closes sessions deterministically.

- [ ] **Step 5: Rewrite public MCP tools as manager adapters**

Preserve server list, tool list, execute, resource list/read, and auth response fields. Replace every mock branch. OAuth tools expose status and authorization URL/callback data supported by the SDK; credentials remain outside durable state.

- [ ] **Step 6: Test HTTP transport, disconnect, cancellation, timeout, child-specific servers, and shutdown**

Run: `.venv/bin/pytest tests/test_mcp_runtime.py tests/test_tool_pipeline.py -q`

- [ ] **Step 7: Run Stage 4 gate and commit**

```bash
git add pyproject.toml uv.lock harness/mcp.py harness/session.py tools/mcp_*.py tests/mcp_test_server.py tests/test_mcp_runtime.py
git commit -m "feat: add real MCP lifecycle"
```

## Stage 5: Worktrees And Deferred Tools

### Task 11: Session-Owned Worktree Manager

**Files:**
- Create: `harness/worktrees.py`
- Rewrite: `tools/worktree_tool.py`
- Modify: `harness/context.py`
- Modify: `harness/session.py`
- Modify: path-aware tools in `tools/file_tools.py`, `tools/bash_tool.py`, `tools/search_tools.py`
- Test: `tests/test_worktree_runtime.py`

- [ ] **Step 1: Write failing temporary-repository tests**

```python
def test_enter_worktree_changes_only_effective_cwd(git_repo, harness):
    process_cwd = Path.cwd()
    record = harness.worktrees.create("feature")
    assert harness.effective_cwd == Path(record.path)
    assert Path.cwd() == process_cwd

def test_remove_fails_closed_for_uncommitted_changes(git_repo, harness):
    record = harness.worktrees.create("feature")
    (Path(record.path) / "dirty.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(WorktreeNotClean):
        harness.worktrees.remove(record.worktree_id, discard_changes=False)
    assert Path(record.path).exists()
```

- [ ] **Step 2: Verify current `os.chdir` implementation fails isolation test**

Run: `.venv/bin/pytest tests/test_worktree_runtime.py -q`

- [ ] **Step 3: Implement safe create, restore, keep, and remove**

Use `git -C <repo>` commands with no global cwd mutation. Validate slug segments, canonical containment, repository identity, branch, base commit, worktree metadata, and owner. Unknown state becomes `orphaned`.

- [ ] **Step 4: Route effective cwd through all path-aware tools**

Relative paths and shell working directories resolve from `SessionHarness.effective_cwd`. Child worktree isolation changes only that child harness unless the root explicitly enters a worktree.

- [ ] **Step 5: Test resume, wrong owner, path traversal, existing branch, keep, discard confirmation, and cleanup failure**

Run: `.venv/bin/pytest tests/test_worktree_runtime.py tests/test_tool_pipeline.py -q`

- [ ] **Step 6: Commit**

```bash
git add harness/worktrees.py harness/context.py harness/session.py tools/worktree_tool.py tools/file_tools.py tools/bash_tool.py tools/search_tools.py tests/test_worktree_runtime.py
git commit -m "feat: isolate session worktrees"
```

### Task 12: Deferred Tool Registry And ToolSearch

**Files:**
- Create: `harness/deferred_tools.py`
- Rewrite: `tools/tool_search_tool.py`
- Modify: `tools/base.py`
- Modify: `query_engine.py`
- Modify: `harness/session.py`
- Test: `tests/test_deferred_tools.py`

- [ ] **Step 1: Write failing visibility and activation tests**

```python
def test_deferred_schema_requires_selection(harness):
    assert "worktree_enter" not in visible_names(harness)
    result = harness.deferred_tools.search("select:worktree_enter")
    assert result.selected == "worktree_enter"
    assert "worktree_enter" in visible_names(harness)

def test_activation_is_restored(harness_factory):
    first = harness_factory.create("s1")
    first.deferred_tools.activate("worktree_enter")
    resumed = harness_factory.resume("s1")
    assert "worktree_enter" in resumed.deferred_tools.visible_names()
```

- [ ] **Step 2: Verify static ToolSearch does not change visible schemas**

Run: `.venv/bin/pytest tests/test_deferred_tools.py -q`

- [ ] **Step 3: Add explicit tool metadata and scoped activation**

Normalize `should_defer` whether implemented as bool or method. Initial model schemas include non-deferred tools plus ToolSearch. Search uses name, description, and search hint. Exact `select:<canonical>` persists activation for the current root or child agent.

- [ ] **Step 4: Integrate dynamic schemas into every model turn**

QueryEngine and AgentExecutor fetch visible schemas from the current harness immediately before each model call. Direct unactivated calls return controlled validation errors. MCP connect/disconnect updates availability without deleting activation history.

- [ ] **Step 5: Test aliases, child isolation, resume, missing MCP server, and exact selection**

Run: `.venv/bin/pytest tests/test_deferred_tools.py tests/test_query_engine_runtime.py tests/test_agent_runtime.py -q`

- [ ] **Step 6: Run the complete core gate and commit**

Run: `.venv/bin/pytest tests/state_core tests/test_session_harness.py tests/test_agent_scheduler.py tests/test_agent_runtime_tools.py tests/test_hook_runtime.py tests/test_skill_runtime.py tests/test_tool_pipeline.py tests/test_budget_runtime.py tests/test_tracing_runtime.py tests/test_context_controller.py tests/test_mcp_runtime.py tests/test_worktree_runtime.py tests/test_deferred_tools.py -q`

```bash
git add harness/deferred_tools.py harness/session.py tools/base.py tools/tool_search_tool.py query_engine.py tests/test_deferred_tools.py
git commit -m "feat: add deferred tool discovery"
```

Do not begin Stage 6 until this complete core gate passes.

## Stage 6: HTTP Compatibility And Cleanup

### Task 13: Stateless Compatibility Services

**Files:**
- Rewrite: `services/conversation_service.py`
- Rewrite: `services/task_service.py`
- Rewrite: `services/plan_service.py`
- Rewrite: `routers/plan.py`
- Rewrite: `routers/agents.py`
- Modify: `routers/data_router.py`
- Modify: `services/chat_stream.py`
- Modify: `main.py`
- Test: `tests/state_core/test_api_compat.py`

- [ ] **Step 1: Write failing HTTP recovery tests**

```python
def test_agent_api_background_lifecycle_survives_new_factory(client, harness_factory):
    response = client.post("/agents", json={
        "description": "Inspect", "prompt": "inspect", "subagent_type": "Explore",
        "run_in_background": True,
    })
    assert response.status_code == 200
    agent_id = response.json()["data"]["agent_id"]
    assert harness_factory.resume(response.json()["data"]["session_id"]).agent_scheduler.status(
        agent_id
    ) is not None

def test_plan_api_write_is_visible_from_new_runtime(client, runtime_factory):
    response = client.post("/api/v1/plans", json={"conversation_id": "s1", "content": "# Plan"})
    assert response.status_code == 200
    assert runtime_factory.resume("s1").state.plan.content == "# Plan"

def test_message_api_appends_one_authoritative_event(client, runtime_factory):
    response = client.post("/api/v1/conversations/s1/messages", json={
        "role": "user", "content": "hello",
    })
    assert response.status_code == 200
    events = runtime_factory.resume("s1").events()
    user_events = [event for event in events if event.event_type is EventType.USER_MESSAGE]
    assert len(user_events) == 1
    assert user_events[0].payload["content"] == "hello"
```

- [ ] **Step 2: Verify legacy dual writes and manager routes fail assertions**

Run: `.venv/bin/pytest tests/state_core/test_api_compat.py -q`

- [ ] **Step 3: Rewrite services as SessionHarnessFactory adapters**

Preserve Pydantic request and response models. Conversation messages, Plan mutations, Task V2, Todo V1, Agent lifecycle, and chat streaming delegate to the harness/state-core. Legacy tables are read only through explicit migration.

- [ ] **Step 4: Replace Plan and Agent router manager calls**

Remove imports of `get_plan_mode_manager`, worker-pool managers, and global spawn managers from primary routes. Save/approve/reject/force-exit and spawn/status/stop all use the session harness.

- [ ] **Step 5: Test all existing endpoints and new-factory recovery**

Run: `.venv/bin/pytest tests/state_core/test_api_compat.py tests/state_core/test_service_adapters.py tests/test_query_engine_runtime.py -q`

- [ ] **Step 6: Commit**

```bash
git add services routers main.py tests/state_core/test_api_compat.py
git commit -m "feat: route APIs through durable harness"
```

### Task 14: Remove Duplicate Primary State And Complete Acceptance

**Files:**
- Modify or remove primary ownership from: `plan/manager.py`, `plan/storage.py`, `tools/plan_mode_tools.py`, `tools/agent_tool.py`, `agents/worker_pool/agent_manager.py`
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-07-24-p1-harness-completion.md`
- Test: entire suite

- [ ] **Step 1: Audit forbidden primary imports**

Run:

```bash
rg -n "get_plan_mode_manager|PlanModeManager|SpawnAgentManager|AgentManager\(|os\.chdir|Mock implementation|目前返回模拟结果" --glob '*.py'
```

Expected: only explicit migration tests, compatibility re-exports, or non-primary examples remain. Each remaining match is documented inline or removed.

- [ ] **Step 2: Delete duplicate behavior, not compatibility names**

Legacy modules become thin imports/adapters when external imports require them. Remove process-local state ownership, dynamic Python skill execution, MCP mock returns, worktree global cwd mutation, and old `ToolError` calls that pass the removed `tool_name` keyword.

- [ ] **Step 3: Update README contracts**

Document SessionHarness, Agent foreground/background tools, Task/Todo modes, Plan approval, skill/hook execution, budgets, compaction, MCP configuration, deferred tools, worktree safety, recovery semantics, and non-replay guarantees.

- [ ] **Step 4: Run complete verification**

```bash
.venv/bin/pytest -q
.venv/bin/python -m compileall -q state_core harness agents plan services tools query_engine.py tests
.venv/bin/ruff check --select E9,F state_core harness agents plan services tools query_engine.py tests
git diff --check
```

Expected: all commands exit zero with no leaked asyncio tasks or test-modified tracked files.

- [ ] **Step 5: Run smoke acceptance**

Run cold resume with an interrupted background Agent, compact recovery, concurrent budget exhaustion, real stdio and HTTP MCP servers, temporary-repository worktree recovery/removal, deferred tool activation after resume, and API mutation recovery from a new factory.

- [ ] **Step 6: Mark plan complete and commit**

```bash
git add README.md docs/superpowers/plans/2026-07-24-p1-harness-completion.md
git commit -m "docs: complete durable harness delivery"
```

- [ ] **Step 7: Push the completed branch**

```bash
git push origin codex/p1-state-core
```

Push only after all verification and acceptance checks pass.
