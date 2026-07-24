# P1 State Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Plan, Task V2, Todo compatibility, and session resume around one durable `SessionRuntime` whose observable behavior follows the Node reference implementation.

**Architecture:** Introduce a focused `state_core` package containing domain types, repository protocols, SQLAlchemy persistence, Plan file storage, migration, and the session facade. Replace the primary Task/Todo/Plan tools and QueryEngine state path instead of patching the existing in-memory managers; retain thin adapters only where HTTP compatibility requires them.

**Tech Stack:** Python 3.10+, asyncio, dataclasses, SQLAlchemy, SQLite, pathlib, pytest, existing ToolRuntime and QueryEngine.

---

## File Structure

- Create `state_core/types.py`: authoritative enums, dataclasses, serialization, and domain errors.
- Create `state_core/repository.py`: storage protocols and optimistic revision contract.
- Create `state_core/sqlalchemy_store.py`: durable sessions, events, snapshots, tasks, high-water marks, and transactions.
- Create `state_core/plan_files.py`: stable plan slug and markdown file behavior.
- Create `state_core/runtime.py`: Plan, Task, Todo, transcript, checkpoint, and resume orchestration.
- Create `state_core/migration.py`: idempotent import from legacy Conversation, Message, Task, and Plan rows.
- Create `state_core/__init__.py`: intentional public API only.
- Rewrite `tools/task_tools.py`: Node-compatible Task V2 tools backed only by `SessionRuntime`.
- Rewrite `tools/todo_tool.py`: Node-compatible legacy TodoWrite backed only by `SessionRuntime`.
- Rewrite `plan/tools.py`: canonical EnterPlanMode and ExitPlanMode tools.
- Rewrite `plan/__init__.py`: export the new state-core Plan types and compatibility accessors.
- Rewrite `tools/plan_mode_tools.py` as an unregistered compatibility re-export after API callers are migrated.
- Modify `tools/base.py`: context-aware tool enablement.
- Modify `tools/__init__.py`: expose one canonical Task/Todo/Plan implementation.
- Rewrite the session-owned portions of `query_engine.py`: durable load, append, checkpoint, resume, and interruption.
- Modify `harness/context.py`: carry the loaded SessionRuntime explicitly in metadata-compatible form.
- Modify `agents/engine.py` and `agents/types.py`: persist child lifecycle references used by resume.
- Modify `models/__init__.py`: register state-core tables without making legacy models authoritative.
- Create `tests/state_core/`: Node-derived domain, persistence, tool, recovery, migration, and QueryEngine tests.

### Task 1: Define The Authoritative Domain Contract

**Node references:** `src/utils/tasks.ts`, `src/utils/todo/types.ts`, `src/utils/plans.ts`

**Files:**
- Create: `state_core/types.py`
- Create: `state_core/repository.py`
- Create: `state_core/__init__.py`
- Create: `tests/state_core/test_types.py`

- [ ] **Step 1: Write failing domain contract tests**

```python
from state_core import PlanState, SessionState, TaskItem, TaskStatus


def test_task_round_trip_preserves_node_fields():
    task = TaskItem(
        id="7",
        subject="Implement store",
        description="Persist runtime state",
        active_form="Implementing store",
        owner="agent-a",
        status=TaskStatus.IN_PROGRESS,
        blocks=["8"],
        blocked_by=["6"],
        metadata={"source": "plan"},
    )
    assert TaskItem.from_dict(task.to_dict()) == task


def test_session_state_round_trip_preserves_plan_and_todos():
    state = SessionState.new("session-1")
    state.plan.state = PlanState.PLANNING
    state.todos["agent-a"] = [
        {"content": "Inspect code", "status": "in_progress", "activeForm": "Inspecting code"}
    ]
    assert SessionState.from_dict(state.to_dict()) == state
```

- [ ] **Step 2: Run the tests and verify the new package is missing**

Run: `.venv/bin/pytest tests/state_core/test_types.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'state_core'`.

- [ ] **Step 3: Implement enums, value objects, and typed errors**

```python
class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class PlanState(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"


class SessionHealth(str, Enum):
    READY = "ready"
    RECOVERY_REQUIRED = "recovery_required"


@dataclass
class TaskItem:
    id: str
    subject: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    active_form: str | None = None
    owner: str | None = None
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class StateCoreError(Exception):
    pass


class RevisionConflict(StateCoreError):
    pass


class InvalidTransition(StateCoreError):
    pass
```

Implement explicit `to_dict()` and `from_dict()` methods. Do not use `asdict()` for persisted schemas because enum and compatibility key conversion must remain stable.

- [ ] **Step 4: Define repository protocols without storage assumptions**

```python
class StateRepository(Protocol):
    def create_session(self, state: SessionState) -> SessionState: ...
    def load_session(self, session_id: str) -> SessionState | None: ...
    def commit(
        self,
        state: SessionState,
        events: list[SessionEvent],
        expected_revision: int,
    ) -> SessionState: ...
    def list_events(self, session_id: str, after_id: int = 0) -> list[SessionEvent]: ...
    def save_snapshot(self, snapshot: SessionSnapshot) -> None: ...
    def latest_snapshot(self, session_id: str) -> SessionSnapshot | None: ...


class TaskRepository(Protocol):
    def create(self, task_list_id: str, task: NewTask) -> TaskItem: ...
    def get(self, task_list_id: str, task_id: str) -> TaskItem | None: ...
    def list(self, task_list_id: str) -> list[TaskItem]: ...
    def update(self, task_list_id: str, task_id: str, mutation: TaskMutation) -> TaskItem | None: ...
    def claim(self, task_list_id: str, task_id: str, owner: str) -> ClaimResult: ...
    def delete(self, task_list_id: str, task_id: str) -> bool: ...
```

- [ ] **Step 5: Run the focused tests**

Run: `.venv/bin/pytest tests/state_core/test_types.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the domain contract**

```bash
git add state_core/types.py state_core/repository.py state_core/__init__.py tests/state_core/test_types.py
git commit -m "feat: define durable session state contract"
```

### Task 2: Build Transactional SQLAlchemy Persistence

**Node reference:** `src/utils/tasks.ts`, especially high-water IDs, lock-protected mutations, and reciprocal dependency updates.

**Files:**
- Create: `state_core/sqlalchemy_store.py`
- Create: `tests/state_core/test_sqlalchemy_store.py`
- Modify: `models/__init__.py`

- [ ] **Step 1: Write failing persistence and concurrency tests**

```python
def test_deleted_task_id_is_not_reused(store):
    first = store.tasks.create("list-1", NewTask("One", "First"))
    assert store.tasks.delete("list-1", first.id)
    second = store.tasks.create("list-1", NewTask("Two", "Second"))
    assert (first.id, second.id) == ("1", "2")


def test_dependency_edges_are_reciprocal(store):
    one = store.tasks.create("list-1", NewTask("One", "First"))
    two = store.tasks.create("list-1", NewTask("Two", "Second"))
    store.tasks.update("list-1", one.id, TaskMutation(add_blocks=[two.id]))
    assert store.tasks.get("list-1", one.id).blocks == [two.id]
    assert store.tasks.get("list-1", two.id).blocked_by == [one.id]


def test_only_one_owner_can_claim(store_factory):
    seed = store_factory().tasks.create("list-1", NewTask("One", "First"))
    results = claim_from_two_independent_sessions(store_factory, seed.id)
    assert sum(result.claimed for result in results) == 1
```

- [ ] **Step 2: Run the tests and verify persistence is absent**

Run: `.venv/bin/pytest tests/state_core/test_sqlalchemy_store.py -q`

Expected: FAIL importing `state_core.sqlalchemy_store`.

- [ ] **Step 3: Define dedicated state-core tables**

Use new table names so legacy rows can be migrated without ambiguous in-place mutation:

```python
class RuntimeSessionRecord(Base):
    __tablename__ = "runtime_sessions"
    session_id = Column(String, primary_key=True)
    revision = Column(Integer, nullable=False, default=0)
    state = Column(JSON, nullable=False)
    migrated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class RuntimeEventRecord(Base):
    __tablename__ = "runtime_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    parent_event_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class RuntimeTaskRecord(Base):
    __tablename__ = "runtime_tasks"
    task_list_id = Column(String, primary_key=True)
    task_id = Column(String, primary_key=True)
    subject = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    active_form = Column(String, nullable=True)
    owner = Column(String, nullable=True)
    status = Column(String, nullable=False)
    blocks = Column(JSON, nullable=False, default=list)
    blocked_by = Column(JSON, nullable=False, default=list)
    metadata_json = Column(JSON, nullable=False, default=dict)
    version = Column(Integer, nullable=False, default=0)
```

Add `RuntimeSnapshotRecord` and `RuntimeTaskCounterRecord`. Register them on the existing `Base`, but keep all state-core query code inside `sqlalchemy_store.py`.

- [ ] **Step 4: Implement optimistic session saves and ordered events**

```python
result = db.execute(
    update(RuntimeSessionRecord)
    .where(RuntimeSessionRecord.session_id == state.session_id)
    .where(RuntimeSessionRecord.revision == expected_revision)
    .values(state=state.to_dict(), revision=expected_revision + 1)
)
if result.rowcount != 1:
    raise RevisionConflict(state.session_id)
```

Event append and state mutation must share the caller's transaction. Never report a successful state change if its event append fails.

- [ ] **Step 5: Implement Task V2 transactions**

Allocate numeric IDs from `RuntimeTaskCounterRecord` under the same write transaction. Implement reciprocal edges, self-edge rejection, missing-reference rejection, delete cleanup, metadata null-as-delete merge, and atomic owner claim.

```python
if task_id in mutation.add_blocks:
    raise InvalidTaskDependency("task cannot block itself")

claimed = db.execute(
    update(RuntimeTaskRecord)
    .where(RuntimeTaskRecord.task_list_id == task_list_id)
    .where(RuntimeTaskRecord.task_id == task_id)
    .where(RuntimeTaskRecord.owner.is_(None))
    .where(RuntimeTaskRecord.status == "pending")
    .values(owner=owner, status="in_progress", version=RuntimeTaskRecord.version + 1)
)
```

- [ ] **Step 6: Run persistence tests and the existing suite**

Run: `.venv/bin/pytest tests/state_core/test_sqlalchemy_store.py -q`

Expected: PASS.

Run: `.venv/bin/pytest -q`

Expected: all existing and new tests PASS.

- [ ] **Step 7: Commit persistence**

```bash
git add models/__init__.py state_core/sqlalchemy_store.py tests/state_core/test_sqlalchemy_store.py
git commit -m "feat: add transactional state persistence"
```

### Task 3: Rebuild Task V2 Tools

**Node references:** all four `src/tools/Task*Tool` modules and their prompts.

**Files:**
- Create: `state_core/runtime.py`
- Replace: `tools/task_tools.py`
- Create: `tests/state_core/test_task_tools_v2.py`
- Modify: `tools/base.py`

- [ ] **Step 1: Write failing Node-contract tool tests**

```python
async def test_task_create_uses_runtime_task_list(tool_runtime_context):
    result = await TaskCreateTool().run(
        {"subject": "Implement", "description": "Build state core", "activeForm": "Implementing"},
        tool_runtime_context,
    )
    assert result.success
    assert result.data == {"taskId": "1", "subject": "Implement"}


async def test_task_update_deleted_is_action_not_state(seed_task, tool_runtime_context):
    result = await TaskUpdateTool().run(
        {"taskId": seed_task.id, "status": "deleted"},
        tool_runtime_context,
    )
    assert result.data["statusChange"] == {"from": "pending", "to": "deleted"}
```

Also test stable list ordering, missing-task benign results, metadata null deletion, dependency updates, and atomic owner conflict.

- [ ] **Step 2: Run and observe mismatches with the legacy service tools**

Run: `.venv/bin/pytest tests/state_core/test_task_tools_v2.py -q`

Expected: FAIL because current tools require legacy database schemas and return different field names.

- [ ] **Step 3: Replace `tools/task_tools.py`**

The replacement tools obtain the runtime only from context:

```python
def require_session_runtime(context: dict[str, Any]) -> SessionRuntime:
    runtime = context.get("session_runtime")
    if not isinstance(runtime, SessionRuntime):
        raise ToolExecutionError("Session runtime is not configured")
    return runtime


@register_tool
class TaskCreateTool(Tool[TaskCreateInput, dict[str, Any]]):
    name = "task_create"
    should_defer = True

    async def run(self, input_data, context):
        parsed = self.input_type(**input_data)
        task = require_session_runtime(context).create_task(
            subject=parsed.subject,
            description=parsed.description,
            active_form=parsed.active_form,
            metadata=parsed.metadata,
        )
        return ToolResult.ok({"taskId": task.id, "subject": task.subject})
```

Use snake_case internally while accepting Node camelCase aliases through input normalization. Tool outputs use Node public field names.

Create the first `SessionRuntime` implementation in this task. It loads a `SessionState`, resolves the task-list ID, delegates Task V2 mutations to `TaskRepository`, and commits the matching domain event through `StateRepository.commit()`.

- [ ] **Step 4: Add context-aware enablement to the tool contract**

Add a normalized `is_enabled(context)` trait. Task V2 tools return enabled only when `SessionRuntime.task_mode == "task_v2"`.

- [ ] **Step 5: Run focused and contract tests**

Run: `.venv/bin/pytest tests/state_core/test_task_tools_v2.py tests/test_tool_contract.py tests/test_tool_runtime.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the Task V2 rewrite**

```bash
git add tools/task_tools.py tools/base.py tests/state_core/test_task_tools_v2.py
git commit -m "feat: rebuild Task V2 tools"
```

### Task 4: Rebuild TodoWrite As A Mutually Exclusive Compatibility Mode

**Node references:** `src/tools/TodoWriteTool/TodoWriteTool.ts`, `src/utils/todo/types.ts`.

**Files:**
- Replace: `tools/todo_tool.py`
- Create: `tests/state_core/test_todo_compat.py`
- Modify: `tools/__init__.py`

- [ ] **Step 1: Write failing compatibility tests**

```python
async def test_todos_are_scoped_by_agent(session_runtime):
    session_runtime.task_mode = "todo_v1"
    session_runtime.replace_todos([todo("Root", "pending")])
    session_runtime.replace_todos([todo("Child", "in_progress")], agent_id="agent-a")
    assert [t["content"] for t in session_runtime.todos()] == ["Root"]
    assert [t["content"] for t in session_runtime.todos("agent-a")] == ["Child"]


async def test_all_completed_clears_stored_list_but_returns_submission(context):
    submitted = [todo("Done", "completed")]
    result = await TodoWriteTool().run({"todos": submitted}, context)
    assert result.data["newTodos"] == submitted
    assert context["session_runtime"].todos() == []
```

Test that Task V2 schemas exclude TodoWrite and Todo V1 schemas exclude all Task V2 tools.

- [ ] **Step 2: Run and verify current TodoWrite is process-local**

Run: `.venv/bin/pytest tests/state_core/test_todo_compat.py -q`

Expected: FAIL because current TodoWrite owns its own dictionary and both tool families remain visible.

- [ ] **Step 3: Replace TodoWrite and filter schemas by runtime mode**

```python
class TodoWriteTool(Tool[TodoWriteInput, dict[str, Any]]):
    name = "todo_write"
    should_defer = True

    def is_enabled(self, context: RuntimeContext | None = None) -> bool:
        runtime = context and context.metadata.get("session_runtime")
        return bool(runtime and runtime.task_mode == "todo_v1")

    async def run(self, input_data, context):
        parsed = self.input_type(**input_data)
        runtime = require_session_runtime(context)
        old_todos = runtime.todos(context.get("agent_id"))
        runtime.replace_todos(parsed.todos, agent_id=context.get("agent_id"))
        return ToolResult.ok({"oldTodos": old_todos, "newTodos": parsed.todos})
```

Update QueryEngine schema construction later to pass RuntimeContext into `ToolRegistry.list_specs(context)`; add registry tests here for the enablement contract.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest tests/state_core/test_todo_compat.py tests/test_tool_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Todo compatibility**

```bash
git add tools/todo_tool.py tools/__init__.py tests/state_core/test_todo_compat.py
git commit -m "feat: add Node-compatible Todo mode"
```

### Task 5: Rebuild Plan State And Canonical Plan Tools

**Node references:** `src/tools/EnterPlanModeTool`, `src/tools/ExitPlanModeTool`, `src/utils/plans.ts`.

**Files:**
- Create: `state_core/plan_files.py`
- Modify: `harness/context.py`
- Replace: `plan/tools.py`
- Replace: `plan/__init__.py`
- Delete after callers migrate: `tools/plan_mode_tools.py`
- Replace with compatibility adapters: `plan/manager.py`, `plan/storage.py`, `plan/types.py`
- Create: `tests/state_core/test_plan_runtime.py`
- Create: `tests/state_core/test_plan_tools.py`

- [ ] **Step 1: Write failing state-machine tests**

```python
def test_plan_approval_restores_pre_plan_mode(session_runtime, approve):
    session_runtime.permission_mode = "bypass"
    entered = session_runtime.enter_plan()
    assert entered.permission_mode == "plan"
    session_runtime.save_plan("# Implement")
    request = session_runtime.submit_plan([{"tool": "Bash", "prompt": "run tests"}])
    assert request.state is PlanState.PENDING_APPROVAL
    session_runtime.approve_plan(edited_content="# Implement safely")
    exited = session_runtime.exit_plan()
    assert exited.permission_mode == "bypass"


def test_incomplete_persisted_plan_fails_closed(reopen_runtime):
    runtime = reopen_runtime({"plan": {"state": "approved", "prePlanMode": None}})
    assert runtime.permission_mode == "plan"
    assert runtime.state.health.value == "recovery_required"
```

Also test reject-to-planning, stable slug on resume, non-empty plan requirement, edited plan persistence, and missing approval callback.

- [ ] **Step 2: Run and verify the in-memory manager diverges**

Run: `.venv/bin/pytest tests/state_core/test_plan_runtime.py -q`

Expected: FAIL because Plan state is not durable and permission restoration is split across QueryEngine and the manager.

- [ ] **Step 3: Implement stable Plan files**

```python
class PlanFileStore:
    def path_for(self, slug: str, agent_id: str | None = None) -> Path:
        suffix = f"-agent-{sanitize(agent_id)}" if agent_id else ""
        return self.root / f"{slug}{suffix}.md"

    def write(self, slug: str, content: str, agent_id: str | None = None) -> Path:
        path = self.path_for(slug, agent_id)
        atomic_write_text(path, content)
        return path
```

Generate and persist the slug on first Plan access. Never regenerate it during resume.

- [ ] **Step 4: Implement Plan transitions inside SessionRuntime**

Every transition validates current state, writes Plan content first when needed, then persists state and the transition event. Approval without a callback remains pending. Invalid recovered state sets `RECOVERY_REQUIRED` and Plan permissions.

Extend `PermissionMode` with Node's `AUTO = "auto"` value so pre-Plan `auto`, `default`, and `bypass` modes can all be restored without lossy string conversion.

- [ ] **Step 5: Replace Plan tools and remove duplicate registration**

`EnterPlanModeTool` calls `session_runtime.enter_plan()`. `ExitPlanModeTool` saves optional edited content, submits approval, invokes the runtime approval callback, and only exits after approval. Rejection returns a structured result while remaining in planning.

Expose only these tools from `tools/__init__.py`; remove `tools.plan_mode_tools` from the registry. Replace `plan/__init__.py` exports with state-core aliases needed by current API imports. Rewrite `plan/manager.py`, `plan/storage.py`, `plan/types.py`, and `tools/plan_mode_tools.py` as thin adapters or re-exports with no process-local state. Preserve compatible configuration-path behavior already present in the worktree.

- [ ] **Step 6: Run Plan, registry, and permission tests**

Run: `.venv/bin/pytest tests/state_core/test_plan_runtime.py tests/state_core/test_plan_tools.py tests/test_tool_contract.py tests/test_tool_runtime.py -q`

Expected: PASS with one canonical EnterPlanMode and ExitPlanMode registration.

- [ ] **Step 7: Commit the Plan rewrite**

```bash
git add state_core/plan_files.py state_core/runtime.py harness/context.py plan/__init__.py plan/manager.py plan/storage.py plan/types.py plan/tools.py tools/plan_mode_tools.py tests/state_core/test_plan_runtime.py tests/state_core/test_plan_tools.py
# Partially stage only the P1 Plan registration hunks from tools/__init__.py.
git add -p tools/__init__.py
git commit -m "feat: rebuild durable Plan mode"
```

### Task 6: Add Transcript Events, Checkpoints, Recovery, And Migration

**Node references:** `src/utils/sessionStorage.ts`, `src/utils/plans.ts`, `src/tasks/LocalMainSessionTask.ts`.

**Files:**
- Modify: `state_core/runtime.py`
- Create: `state_core/migration.py`
- Create: `tests/state_core/test_session_recovery.py`
- Create: `tests/state_core/test_legacy_migration.py`

- [ ] **Step 1: Write failing event replay and interruption tests**

```python
def test_snapshot_plus_events_rebuilds_exact_tool_history(runtime_factory):
    runtime = runtime_factory()
    runtime.append_user_message("Inspect")
    runtime.checkpoint()
    runtime.append_assistant_message("", tool_calls=[tool_call("call-1", "read_file")])
    runtime.append_tool_result("call-1", "read_file", {"content": "ok"})
    resumed = runtime_factory(session_id=runtime.session_id).resume()
    assert resumed.conversation_turns() == runtime.conversation_turns()


def test_running_work_becomes_interrupted_without_replay(runtime_factory):
    runtime = runtime_factory()
    runtime.record_execution_started("tool", "call-1", mutating=True)
    resumed = runtime_factory(session_id=runtime.session_id).resume()
    assert resumed.execution("call-1").status == "interrupted"
    assert resumed.pending_replays == []
```

Also test corrupt snapshot fallback, corrupt event recovery-required state, stable event ordering, and exact tool call IDs.

- [ ] **Step 2: Run and verify recovery behavior is absent**

Run: `.venv/bin/pytest tests/state_core/test_session_recovery.py -q`

Expected: FAIL because current QueryEngine conversations exist only in memory.

- [ ] **Step 3: Implement event reducers and checkpoints**

Use an explicit reducer table rather than conditional patches:

```python
EVENT_REDUCERS: dict[EventType, Callable[[SessionState, SessionEvent], None]] = {
    EventType.USER_MESSAGE: reduce_user_message,
    EventType.ASSISTANT_MESSAGE: reduce_assistant_message,
    EventType.TOOL_RESULT: reduce_tool_result,
    EventType.PLAN_TRANSITION: reduce_plan_transition,
    EventType.TASK_MUTATION: reduce_task_mutation,
    EventType.TODO_REPLACED: reduce_todo_replaced,
    EventType.EXECUTION_INTERRUPTED: reduce_execution_interrupted,
}
```

Snapshots contain the last applied event ID. On corrupt snapshot, replay from event zero. On an invalid event, stop, mark recovery-required, and deny writes.

- [ ] **Step 4: Implement restart interruption**

After replay and before returning a resumed runtime, convert every non-terminal execution and child Agent to `interrupted`, append one event per conversion, and checkpoint. Never invoke ToolRuntime during recovery.

- [ ] **Step 5: Implement idempotent legacy migration**

Read existing Conversation and Message rows in timestamp order. Preserve message IDs as source IDs in event payloads. Import a valid legacy Plan and Tasks into the new model, set `migrated_at`, and skip migration on subsequent resumes.

```python
if record.migrated_at is not None:
    return MigrationResult(already_migrated=True)
```

Ambiguous Plan/Task rows produce diagnostics and remain read-only rather than being guessed into a writable state.

- [ ] **Step 6: Run recovery and migration tests**

Run: `.venv/bin/pytest tests/state_core/test_session_recovery.py tests/state_core/test_legacy_migration.py -q`

Expected: PASS.

- [ ] **Step 7: Commit recovery and migration**

```bash
git add state_core/runtime.py state_core/migration.py tests/state_core/test_session_recovery.py tests/state_core/test_legacy_migration.py
git commit -m "feat: add durable session resume"
```

### Task 7: Rewrite QueryEngine Around SessionRuntime

**Node references:** `src/QueryEngine.ts`, `src/query.ts`, and session initialization in `src/tasks/LocalMainSessionTask.ts`.

**Files:**
- Replace session ownership sections: `query_engine.py`
- Modify: `harness/context.py`
- Modify: `agents/types.py`
- Modify: `agents/engine.py`
- Create: `tests/state_core/test_query_engine_state_core.py`
- Modify: `tests/test_query_engine_runtime.py`
- Modify: `tests/test_agent_runtime.py`

- [ ] **Step 1: Write failing QueryEngine new-session and resume tests**

```python
async def test_new_engine_instance_resumes_tool_history(engine_factory, fake_llm):
    first = engine_factory(fake_llm)
    session_id = first.create_conversation("session-1")
    await collect(first.chat(session_id, "Inspect"))

    second = engine_factory(fake_llm)
    resumed = second.resume_conversation(session_id)
    assert resumed.to_llm_messages() == first.get_conversation(session_id).to_llm_messages()


async def test_plan_mode_and_tool_visibility_come_from_loaded_state(engine_factory):
    engine = engine_factory()
    session_id = engine.create_conversation("session-2", task_mode="todo_v1")
    engine.session_runtime(session_id).enter_plan()
    names = openai_tool_names(engine._build_tools_schema(session_id))
    assert "write_file" not in names
    assert "todo_write" in names
    assert "task_create" not in names
```

- [ ] **Step 2: Run and verify QueryEngine is still process-local**

Run: `.venv/bin/pytest tests/state_core/test_query_engine_state_core.py -q`

Expected: FAIL because `_conversations` and `_plan_mode_manager` are authoritative.

- [ ] **Step 3: Replace QueryEngine session ownership**

Inject a `SessionRuntimeFactory`. Keep only a cache of loaded runtime handles, never authoritative state.

```python
def create_conversation(self, conversation_id=None, *, task_mode="task_v2") -> str:
    runtime = self._session_factory.create(conversation_id, task_mode=task_mode)
    self._sessions[runtime.session_id] = runtime
    return runtime.session_id


def resume_conversation(self, conversation_id: str) -> ConversationContext:
    runtime = self._session_factory.resume(conversation_id)
    self._sessions[conversation_id] = runtime
    return ConversationContext.from_session_state(runtime.state)
```

Delete `_plan_mode_manager` and direct in-memory Plan queries. `_build_tools_schema()` derives mode and enabled tools from the loaded runtime.

- [ ] **Step 4: Append transcript events at exact lifecycle points**

Append the user event before LLM invocation, assistant event before tool execution, each tool result after completion, and a checkpoint at stable turn completion. Preserve assistant `tool_calls` and result IDs exactly.

- [ ] **Step 5: Pass SessionRuntime through RuntimeContext**

```python
runtime_context = RuntimeContext(
    session_id=conversation_id,
    permission_mode=runtime.permission_mode,
    metadata={"session_runtime": runtime, "agent_id": agent_id},
    cancellation=token,
    workspace_root=self.workspace_root,
    approval_callback=self.approval_callback,
)
```

Child Agent contexts inherit the same root session runtime and use their Agent ID for Todo and lifecycle scoping.

- [ ] **Step 6: Remove old primary state paths**

Remove QueryEngine use of `PlanModeManager`, `ConversationService`, `TaskService`, and process-local Todo storage. HTTP compatibility services remain temporarily importable and are unconditionally rewritten as `SessionRuntimeFactory` adapters in Task 8.

- [ ] **Step 7: Run QueryEngine, Agent, and state-core tests**

Run: `.venv/bin/pytest tests/state_core tests/test_query_engine_runtime.py tests/test_agent_runtime.py -q`

Expected: PASS.

- [ ] **Step 8: Commit QueryEngine integration**

```bash
git add query_engine.py harness/context.py agents/types.py agents/engine.py tests/state_core/test_query_engine_state_core.py tests/test_query_engine_runtime.py tests/test_agent_runtime.py
git commit -m "feat: run QueryEngine on durable session state"
```

### Task 8: Compatibility Endpoints, Cleanup, And Stage Verification

**Files:**
- Modify: `main.py`
- Replace delegation only: `services/conversation_service.py`
- Replace delegation only: `services/task_service.py`
- Replace delegation only: `services/plan_service.py`
- Verify compatibility-only status: `plan/manager.py`, `plan/storage.py`, `plan/types.py`, `tools/plan_mode_tools.py`
- Create: `tests/state_core/test_api_compat.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing API compatibility tests**

Test create/list/get/delete conversation, message history, Task endpoints, Plan approval/rejection, and resume using a temporary database. Assert all operations are visible through a newly constructed `SessionRuntimeFactory`.

```python
def test_legacy_task_endpoint_writes_authoritative_task_store(client, runtime_factory):
    response = client.post("/tasks", json={"conversation_id": "s1", "subject": "One", "description": "First"})
    assert response.status_code == 200
    assert runtime_factory.resume("s1").list_tasks()[0].subject == "One"
```

- [ ] **Step 2: Run and identify remaining legacy ownership**

Run: `.venv/bin/pytest tests/state_core/test_api_compat.py -q`

Expected: FAIL at endpoints still writing legacy tables directly.

- [ ] **Step 3: Replace service implementations with adapters**

Services may translate existing Pydantic schemas, but every mutation delegates to the state-core repository. Do not dual-write legacy and state-core tables. Read-only legacy import happens only through `state_core.migration`.

- [ ] **Step 4: Remove dead duplicate implementations**

Use `rg` to prove no primary caller imports the old managers and tools:

Run: `rg -n "PlanModeManager|get_plan_mode_manager|tools\.plan_mode_tools|TaskService\(" --glob '*.py'`

Expected: no primary runtime imports; any remaining matches are explicit migration or compatibility tests.

Require each remaining legacy module to be a stateless adapter or re-export after the import audit and API tests pass. Do not alter unrelated configuration-path or Skill changes already present in the worktree.

- [ ] **Step 5: Document the new runtime and resume contract**

Update README architecture, state locations, Task V2/Todo selection, Plan approval behavior, and resume interruption semantics. State explicitly that mutating operations are never automatically replayed.

- [ ] **Step 6: Run complete first-stage verification**

Run: `.venv/bin/pytest -q`

Expected: all tests PASS.

Run: `.venv/bin/python -m compileall -q state_core harness agents plan services tools query_engine.py tests`

Expected: exit 0.

Run: `.venv/bin/ruff check --select E9,F state_core harness query_engine.py tools/task_tools.py tools/todo_tool.py plan tests/state_core`

Expected: no new E9/F errors.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 7: Run the Node-parity acceptance audit**

Confirm all acceptance points from `docs/superpowers/specs/2026-07-24-p1-state-core-design.md`: durable Plan/Task/Todo/transcript state, exact tool history, interrupted recovery, one canonical Plan tool pair, mutually exclusive Task/Todo modes, and no in-memory manager in the primary path.

- [ ] **Step 8: Commit first-stage completion**

```bash
git add README.md main.py services/conversation_service.py services/task_service.py services/plan_service.py tests/state_core/test_api_compat.py
git commit -m "feat: complete P1 durable state core"
```

Do not push yet. Continue with separately designed and approved P1 Skills, hooks/budgets, MCP, observability, and worktree deliveries. Push only after the complete P1 suite passes, as requested.
