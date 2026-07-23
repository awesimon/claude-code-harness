# P1 State Core Design

## Goal

Rebuild the Python Plan, Task V2, Todo compatibility, and session-resume core so
its behavior follows the Node implementation in the parent repository. The Node
code is the behavioral reference; the Python implementation may use Python-native
persistence and APIs where those choices do not change observable semantics.

This is the first independently testable P1 delivery. Skills, hooks and budgets,
MCP lifecycle, observability, and worktree isolation remain separate follow-up
deliveries and must not be mixed into this implementation.

## Rewrite Policy

This work must not preserve a divergent implementation by layering fixes over it.
When an existing Python module has a different state model, ownership boundary, or
lifecycle than the Node reference, replace that module behind a deliberate public
interface. Compatibility code is allowed only at an external boundary and must
have a removal path.

In particular:

- There will be one authoritative Plan state machine.
- There will be one authoritative Task V2 repository.
- TodoWrite will be a compatibility mode, not a second task database.
- QueryEngine will consume a session runtime interface rather than coordinating
  Plan, Todo, Task, and transcript services independently.
- Existing database models may be migrated or replaced when they cannot express
  the Node behavior cleanly.

## Node Reference Map

The implementation must derive behavior from these Node modules:

- `src/utils/tasks.ts`
- `src/tools/TaskCreateTool/TaskCreateTool.ts`
- `src/tools/TaskGetTool/TaskGetTool.ts`
- `src/tools/TaskListTool/TaskListTool.ts`
- `src/tools/TaskUpdateTool/TaskUpdateTool.ts`
- `src/tools/TodoWriteTool/TodoWriteTool.ts`
- `src/tools/EnterPlanModeTool/EnterPlanModeTool.ts`
- `src/tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`
- `src/utils/plans.ts`
- `src/utils/sessionStorage.ts`
- `src/tasks/LocalMainSessionTask.ts`

Node UI rendering and Bun-specific implementation details are not requirements.
State transitions, ownership, persistence, tool results, interruption semantics,
and permission restoration are requirements.

## Selected Architecture

Use behavioral parity with Python persistence adapters. Domain state machines live
in a new state-core package and depend on repository protocols. SQLAlchemy and
filesystem implementations sit behind those protocols. QueryEngine and tools use
one `SessionRuntime` facade.

The alternative of duplicating Node's per-task JSON files was rejected because it
would create a second persistence model beside the Python API database. Patching
the current managers in place was rejected because the current Plan, Task, Todo,
and conversation modules own overlapping state and cannot provide deterministic
resume behavior.

## Components

### SessionRuntime

`SessionRuntime` is the ownership boundary for durable execution state. It exposes
operations for loading a session, appending transcript events, checkpointing state,
entering and resolving Plan mode, selecting Task V2 or Todo compatibility mode,
and marking unfinished work interrupted during recovery.

It contains no FastAPI or UI behavior. QueryEngine receives it as a dependency and
uses the loaded `SessionState` to construct `RuntimeContext`.

### SessionState

The durable session snapshot contains:

- session ID and monotonically increasing revision;
- current and pre-Plan permission modes;
- Plan state, slug, file path, allowed prompts, and approval metadata;
- task-list ID and selected task mode;
- Todo lists keyed by root session ID or child Agent ID;
- transcript cursor and last durable event ID;
- parent/child Agent references and terminal status;
- creation, update, and interruption timestamps.

Ephemeral objects such as asyncio tasks, callbacks, database sessions, and open
subprocess handles are never serialized.

### Task V2

Task V2 is authoritative when enabled. A task contains the Node-compatible fields
`id`, `subject`, `description`, `active_form`, `owner`, `status`, `blocks`,
`blocked_by`, and `metadata`.

Supported states are `pending`, `in_progress`, and `completed`. `deleted` is an
update action, not a stored state. Task IDs are monotonically increasing within a
task list and are not reused after deletion or reset.

Mutations run in one repository transaction and preserve these invariants:

- `A.blocks` contains `B` exactly when `B.blocked_by` contains `A`.
- A task cannot block itself.
- Duplicate edges are ignored.
- References to missing tasks are rejected.
- Claiming an unowned task and changing it to `in_progress` is atomic.
- A conflicting claim returns the current owner without overwriting it.
- Deleting a task removes all reverse dependency edges.
- Listing is stable by numeric task ID and exposes blocked availability.

Task-list resolution follows Node precedence where applicable: explicit runtime
task-list ID, team context, then session ID.

### Todo Compatibility

TodoWrite remains available only when Task V2 is disabled for the runtime. The two
modes are mutually exclusive and are not synchronized.

Todo lists are scoped by Agent ID when present, otherwise by session ID. Every
update replaces the complete list. When every item is completed, the stored list
is cleared while the tool result still reports the submitted list. Todo operations
never require mutation approval, matching the Node tool behavior.

The compatibility layer implements the Node input and output contract but stores
data through `SessionRuntime`; it does not call Task V2 services.

### Plan State Machine

The authoritative transitions are:

```text
idle -> planning -> pending_approval -> approved -> idle
                     |
                     +-> planning
```

Entering Plan mode records the current permission mode and switches the runtime to
read-only Plan permissions. Re-entering while planning is idempotently rejected.

Submitting a plan requires non-empty persisted content. Exiting from planning
creates an approval request; it cannot silently skip approval. Approval may include
edited plan content and semantic allowed prompts. Rejection restores `planning`
without discarding the plan. A successful exit restores the recorded pre-Plan mode
and clears transient approval state.

If persisted state is incomplete or invalid, the runtime fails closed in Plan mode
rather than restoring write permission.

Plans use a stable per-session slug. Resume reuses the slug and file. A future fork
operation must copy content to a new slug, but session forking is outside this
delivery.

### Transcript And Checkpoints

The session transcript is an append-only event stream. Each event has an ID,
session ID, timestamp, type, payload, and optional parent event ID. Required event
types cover user messages, assistant messages, tool calls, tool results, Plan
transitions, Task/Todo mutations, Agent lifecycle changes, checkpoints, and
interruptions.

Snapshots accelerate loading but are not authoritative. A snapshot records the
last applied event ID and a complete `SessionState`. Resume loads the latest valid
snapshot, replays subsequent events in order, and validates the resulting state.
If a snapshot is corrupt, replay starts from the event log.

Assistant tool-call messages and matching tool results retain their original IDs
and ordering. Resume must not regenerate IDs or replay completed tool calls.

### Interruption Semantics

Asyncio tasks and subprocesses cannot survive a process restart. During resume,
any persisted Agent or tool execution with a non-terminal status becomes
`interrupted`. The runtime appends an interruption event and exposes the prior
operation for inspection. It never automatically reruns a mutating tool.

Read-only work may be retried only through an explicit future retry operation; it
is not retried implicitly in this delivery.

## Data Flow

For a new request:

1. QueryEngine loads or creates `SessionRuntime`.
2. The runtime reconstructs `SessionState` from a checkpoint and event replay.
3. QueryEngine builds `RuntimeContext` from that state.
4. LLM and tool events are appended in execution order.
5. Plan, Task, and Todo tools mutate state only through `SessionRuntime`.
6. Successful domain mutations append events in the same transaction as their
   durable state change.
7. A checkpoint is written at stable turn boundaries and before clean shutdown.

For resume, the same flow starts at step 2 and first converts unfinished work to
`interrupted` before accepting a new user turn.

## Persistence And Concurrency

Repository protocols separate domain behavior from storage. The initial durable
implementation should use the existing SQLAlchemy database for structured session,
task, event, and snapshot records. Plan markdown remains a file artifact referenced
by durable Plan metadata.

Task claims, dependency mutations, event append, and checkpoint revision updates
must use database transactions. Optimistic revision checks prevent two writers from
silently overwriting the same session state. A revision conflict is returned as a
structured runtime error and may be retried by the caller after reload.

No process-local dictionary is an authoritative store.

## Error Handling

- Invalid state transitions return typed domain errors.
- Missing tasks are benign structured failures, matching Node tool behavior.
- Dependency invariant violations abort the transaction.
- Transcript append failures prevent the associated state mutation from being
  reported as successful.
- Corrupt snapshots are quarantined and recovered through event replay.
- Corrupt event streams stop at the last valid event and mark the session as
  recovery-required; write operations remain denied.
- Approval state without a usable approval callback remains pending and fail-closed.
- Resume never reports interrupted execution as completed.

## Compatibility And Migration

Existing public tool aliases remain valid. Canonical Python names stay lower snake
case while Node-style names resolve through the registry.

The migration reads existing conversations and messages into the new event model on
first resume. It must be idempotent and record a migration marker. Existing Task and
Plan records are migrated only when they can be validated; ambiguous records remain
read-only and produce a recovery diagnostic.

Old managers and services are removed from the primary execution path once the new
runtime passes parity tests. Thin adapters may remain for API endpoints during one
compatibility cycle, but they must delegate to the new state core.

## Testing

Tests are derived from Node behavior rather than the current Python implementation.
They cover:

- Task creation with monotonic non-reused IDs;
- atomic task claims and conflicting owners;
- reciprocal dependency creation, removal, and delete cleanup;
- stable task listing and blocked availability;
- mutually exclusive Task V2 and Todo tool exposure;
- per-session and per-Agent Todo isolation;
- Todo clear-on-all-completed behavior;
- complete Plan approval, rejection, edit, and permission restoration transitions;
- fail-closed recovery of incomplete Plan state;
- transcript preservation of assistant tool calls and tool results;
- snapshot plus event replay and corrupt-snapshot fallback;
- restart conversion of running tools and Agents to interrupted;
- migration idempotency for existing conversations;
- QueryEngine end-to-end new-session and resume flows.

Repository tests use temporary SQLite databases and temporary plan directories.
Concurrency tests use independent database sessions. No test requires network
access or a live LLM provider.

## Acceptance Criteria

- Node-derived behavior tests pass for Plan, Task V2, Todo compatibility, and
  session resume.
- QueryEngine has one durable session-state dependency.
- Plan, Task, Todo, and transcript state survive process reconstruction.
- Unfinished work resumes as interrupted without side-effect replay.
- Task/Todo mode selection matches Node semantics.
- No old in-memory manager remains authoritative in the primary harness.
- Full existing tests pass without modifying unrelated user changes.

## Out Of Scope

- Skills discovery and injection;
- lifecycle hooks and budget enforcement;
- MCP server lifecycle;
- observability UI and distributed tracing;
- Git worktree creation and merge;
- automatic retry of interrupted work;
- session fork and remote-session transport behavior.
