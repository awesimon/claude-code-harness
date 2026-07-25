# P1 Durable Harness Completion Design

## Status

Approved design for completing the Python agent scheduling harness while preserving the
existing HTTP and tool contracts. Core runtime work is completed and verified before the
HTTP compatibility layer is rewritten.

## Objective

Complete the Python implementation as a durable, session-scoped agent orchestration
framework with:

- foreground and background subagents;
- durable Plan, Task V2, Todo V1, transcript, agent, and runtime state;
- one controlled tool-call pipeline;
- Agent Skills and hooks integrated into that pipeline;
- context compaction, budgets, and structured observability;
- real MCP client lifecycles;
- session-owned worktree isolation;
- Node-style deferred tool discovery; and
- backward-compatible HTTP and tool interfaces.

The implementation follows the core lifecycle semantics in the Node reference under
`../src`, without copying UI-specific, product-gated, remote execution, teammate, or
telemetry-exporter behavior that is outside this Python service's scope.

## Constraints

1. `state_core` is the only authoritative mutable state store.
2. Public HTTP routes, tool names, and request/response shapes remain compatible.
3. Internal implementations may be rewritten. Legacy managers and dual-write paths are
   removed when their replacements pass acceptance tests.
4. No mutating LLM or tool operation is automatically replayed after process recovery.
5. All filesystem, subprocess, MCP, hook, and subagent execution crosses the same
   permission, timeout, cancellation, budget, and transcript boundary.
6. The dirty primary worktree is not modified. Work proceeds in `codex/p1-state-core`.
7. Core runtime completion is an explicit gate before HTTP compatibility work begins.

## Reference Semantics

The relevant Node implementation is used as the behavioral reference:

- `src/tools/AgentTool`, `src/tasks/LocalAgentTask`, `TaskOutputTool`, and `TaskStopTool`
  for foreground/background agent lifecycle;
- `src/utils/hooks` for ordered hook execution and hook-specific outputs;
- `src/tools/SkillTool`, `DiscoverSkillsTool`, and skill hook registration for skill
  discovery and invocation;
- `src/services/compact` and `src/query/tokenBudget.ts` for compaction and budget control;
- `src/tools/MCPTool`, MCP resource tools, and MCP connection services for MCP lifecycle;
- `src/tools/ToolSearchTool` and `Tool.shouldDefer` for deferred schemas; and
- `src/tools/EnterWorktreeTool`, `ExitWorktreeTool`, and `src/utils/worktree.ts` for
  ownership and fail-closed cleanup.

Python-specific APIs, persistence, and asynchronous primitives remain idiomatic Python.

## Architecture

### SessionHarness

`SessionHarness` is the session-scoped composition root. QueryEngine and every subagent
receive a harness or a child scope created from it. The harness owns references to:

- `SessionRuntime`;
- `ToolRuntime`;
- `AgentScheduler`;
- `HookRuntime`;
- `SkillResolver`;
- `ContextController`;
- `BudgetController`;
- `TraceRecorder`;
- `MCPConnectionManager`;
- `WorktreeManager`;
- `DeferredToolRegistry`;
- permission and approval callbacks;
- cancellation scope; and
- effective session and agent working directories.

No runtime service may create an unrelated global state manager. Process-level registries
may cache immutable definitions or live transport handles, but durable identity and state
always come from the session harness.

`SessionHarness.child(agent_id)` inherits the root session, persistence store, permission
policy, tool runtime, budget hierarchy, skill and MCP configuration, and trace recorder. It
creates an agent-scoped cancellation token, Todo scope, activated-tool set, and effective
cwd.

### QueryEngine

QueryEngine remains the public conversation engine but delegates lifecycle work to the
harness. It is responsible for:

1. loading or creating a session harness;
2. appending user and assistant transcript events;
3. invoking the context controller before model calls;
4. invoking the model under a budget and trace span;
5. routing tool calls through `ToolRuntime`; and
6. checkpointing stable state at turn boundaries.

QueryEngine does not directly execute hooks, skills, MCP calls, worktree commands, or
subagents.

## Durable State Model

The existing session, event, Plan, Task, and Todo records remain authoritative. The state
store is extended with four logical record families.

### Agent Records

An agent record contains:

- stable agent ID and type;
- immutable definition snapshot and model selection;
- root session ID and optional parent agent ID;
- foreground/background execution mode;
- lifecycle status;
- prompt and short description;
- creation, start, completion, and update timestamps;
- effective cwd and optional worktree ID;
- output artifact reference;
- accumulated usage and tool-call count;
- termination reason and structured error; and
- monotonically increasing revision.

Lifecycle transitions are validated by the repository. The canonical states are
`pending`, `running`, `completed`, `failed`, `cancelled`, `timed_out`, `interrupted`, and
`orphaned`. Terminal records cannot return to a running state.

### Runtime Events

Runtime events extend the transcript with typed records for:

- hook start/result/failure;
- skill resolution;
- budget reservation/consumption/exhaustion;
- compaction start/boundary/failure;
- MCP connect/disconnect/call;
- deferred tool activation;
- worktree create/restore/keep/remove/orphan; and
- agent lifecycle updates.

Payloads must pass the existing JSON normalization boundary before persistence.

### Runtime Snapshots

Session checkpoints include:

- current budget limits and consumption;
- activated deferred tools per root and child agent;
- resolved skill versions and content digests;
- effective MCP configuration digests and server status;
- compact boundary and summary metadata;
- active worktree ownership metadata; and
- current termination state.

Snapshots accelerate recovery but never replace the append-only event history.

### Trace Spans

Structured spans cover model, tool, agent, hook, MCP, compaction, and worktree operations.
Each span records stable IDs, parent span ID, session and agent IDs, operation name, start
and finish timestamps, status, duration, usage where applicable, and a sanitized error.
Export is optional; durable local recording is required.

## Agent Scheduler V2

The existing process-local `SpawnAgentManager` is replaced by a durable scheduler.

### Public Operations

- `spawn`: create an agent record and run foreground or background execution;
- `wait`: wait for a running local task or return a durable terminal result;
- `status`: return durable lifecycle and progress;
- `stop`: request cancellation and persist the terminal result;
- `list`: list agents by root session, parent, status, or execution mode; and
- `reconcile`: mark stale in-flight records interrupted during startup or resume.

The existing Agent tool gains compatible optional fields for description and background
execution. Canonical TaskOutput and TaskStop-style tools expose wait/status/output and stop
to the model while existing agent listing or destroy aliases delegate to the same scheduler.

### Execution

The scheduler creates a child harness and executes the subagent query loop with the
definition snapshot. Child tools are resolved from the definition's allowed and denied
tools after skills and MCP requirements are applied. Nested agents use the same root
session and set `parent_agent_id`.

Foreground execution returns the terminal agent result. Background execution returns a
durable launch result immediately and registers an asyncio task. Completion persists the
result before sending any notification.

### Concurrency And Cancellation

The scheduler enforces configurable root-session and per-parent concurrency limits.
Queueing is durable as `pending`. Cancellation is cooperative through the child token and
force-cancels the local asyncio task after a bounded grace period. Tool subprocesses and
MCP requests receive the same cancellation scope.

### Recovery

On process startup or session resume, `pending` and `running` records without a matching
live task become `interrupted`. Their existing transcript and output remain readable. They
are not restarted automatically. A future explicit retry creates a new agent record linked
to the prior record instead of mutating it back to running.

## Controlled Tool Pipeline

Every tool call follows this order:

1. resolve the canonical tool and confirm deferred activation;
2. normalize and validate input against the tool schema;
3. execute matching `PreToolUse` hooks;
4. apply hook input updates and reject a hook block;
5. evaluate permission and approval policy;
6. reserve tool-call and time budget;
7. execute with timeout and cancellation;
8. normalize the result to detached JSON;
9. execute `PostToolUse` or `PostToolUseFailure` hooks;
10. apply permitted hook result updates;
11. consume or release budget reservations;
12. persist exact tool call, result, hook events, and trace span; and
13. return the normalized result to the model.

The pipeline produces one terminal result for every accepted tool call, including timeout,
cancellation, hook rejection, permission denial, malformed input, and serialization error.

## Hooks

`HookRuntime` replaces configuration-only behavior with execution. It loads a stable
configuration snapshot for the session and resolves hooks by event and matcher.

Initial supported events are:

- `SessionStart` and `SessionEnd`;
- `UserPromptSubmit`;
- `PreToolUse`, `PostToolUse`, and `PostToolUseFailure`;
- `PermissionRequest` and `PermissionDenied`;
- `SubagentStart` and `SubagentStop`;
- `PreCompact` and `PostCompact`;
- `WorktreeCreate` and `WorktreeRemove`; and
- `Stop` and `StopFailure`.

Command hooks execute as subprocesses with structured JSON stdin, captured stdout/stderr,
an environment allowlist, timeout, output-size limit, and cancellation. Hook output is
parsed as structured JSON. A Pre hook may allow, block, or update input. A Post hook may
attach metadata or update an MCP result where the contract permits. Failure policy is
explicit per hook; the default is fail-open for observational Post hooks and fail-closed
for permission or mutation-gating Pre hooks.

Hook execution is recursive-safe: hook subprocesses do not trigger tool hooks.

## Agent Skills

Skills use progressive discovery:

1. index only validated name, description, location, digest, and metadata;
2. expose discovery results without injecting all skill bodies;
3. resolve a skill explicitly or from an Agent definition;
4. snapshot its digest and effective metadata into the session;
5. inject its instructions into that agent's system prompt; and
6. merge allowed tools, hooks, and MCP requirements through the harness.

Skill files are constrained to their canonical base directory. Referenced resources must
pass path containment checks. Skill scripts are never executed by unrestricted Python
`exec`; they run through the normal Bash or subprocess tool boundary with permission,
timeout, cancellation, budget, cwd, and transcript controls.

Resume uses the recorded skill snapshot for semantic stability. If the source skill has
changed, the new version is not silently substituted during the existing session.

## Context And Compaction

`ContextController` is invoked before each model request.

1. It performs deterministic micro-compaction of redundant progress and oversized stored
   tool results without changing the durable raw transcript.
2. It estimates the resulting context tokens.
3. Above the configured threshold it fires `PreCompact`, generates a summary using a
   dedicated model budget, persists a compact boundary and summary, fires `PostCompact`,
   and rebuilds the active model context.
4. A failed compaction leaves the prior active context intact and returns a classified
   error or retry decision.

Recovery reconstructs the active context from the latest valid compact boundary plus later
events. Raw pre-boundary events remain auditable.

## Budgets

`BudgetController` supports limits at root session and child agent scope:

- input, output, and total model tokens;
- estimated monetary cost;
- model turns;
- tool calls;
- wall-clock duration; and
- compaction model usage.

Child consumption rolls up to the root. Reservations prevent concurrent background agents
from individually passing a limit and collectively exceeding it. Actual usage reconciles a
reservation. Exhaustion terminates only the affected scope unless the root limit is
exhausted, in which case all children receive cancellation.

Budget state and consumption events are durable. The canonical termination reason is
`budget_exhausted` with the exhausted dimension in structured details.

## Observability

The runtime records spans and structured lifecycle events locally. It exposes session and
agent usage summaries through internal services and later through compatible HTTP routes.
Logging must not include prompts, tool payloads, credentials, or hook environment secrets
unless an explicit safe field is recorded.

The required termination reasons are:

- `completed`;
- `cancelled`;
- `timed_out`;
- `budget_exhausted`;
- `permission_denied`;
- `hook_blocked`;
- `mcp_unavailable`;
- `interrupted`;
- `orphaned`; and
- `failed`.

## MCP Lifecycle

`MCPConnectionManager` owns real clients scoped to the session harness. It supports stdio
and streamable HTTP transports. SSE may be supported when the selected Python MCP SDK
provides it without a second transport abstraction.

The manager provides:

- validated server configuration;
- lazy connect and explicit disconnect;
- tool discovery and dynamic registration;
- resource listing and reading;
- prompt discovery where supported;
- request timeout and cancellation;
- bounded reconnect for transient transport failures;
- OAuth status and authorization hooks for HTTP servers; and
- deterministic shutdown at session end.

Agent-specific MCP servers are additive to inherited root servers and are closed when the
child harness exits. MCP structured content and metadata pass through JSON normalization.
Credentials and live connection objects are never persisted; config digests and lifecycle
status are.

Tests use real in-process or subprocess MCP test servers. Mock return strings are removed.

## Worktree Isolation

`WorktreeManager` is session-owned and never calls `os.chdir`. It computes an effective cwd
passed explicitly to filesystem and subprocess tools through the harness.

A worktree record contains repository root, canonical path, branch, base commit, owner
session, optional owner agent, creation mode, and lifecycle status. Names reject path
traversal and unsafe ref characters.

Create and restore validate:

- repository identity;
- canonical containment under the configured worktree root;
- Git worktree metadata;
- branch and base commit; and
- session or agent ownership.

Keep preserves the worktree and detaches it from active session state. Remove is
fail-closed: unknown Git state, uncommitted files, or unmerged commits require explicit
discard confirmation. Recovery that cannot prove ownership marks the record `orphaned` and
never deletes it automatically.

## Deferred Tool Discovery

`DeferredToolRegistry` separates available tool definitions from schemas visible to the
model. Non-deferred tools are present on the first turn. Deferred tools expose only name
and search hints through ToolSearch.

ToolSearch supports keyword search and exact `select:<tool-name>` activation. Successful
selection adds the full schema to the current root or child agent's visible set. Activation
events are durable and restored with the session. A direct call to an unactivated deferred
tool returns a controlled validation result rather than executing it.

MCP tools join the registry when their server connects. A disconnected server keeps its
activation history but calls fail as `mcp_unavailable` until reconnection succeeds.

## API Compatibility Phase

API work begins only after all core acceptance suites pass.

Conversation, Task, Plan, Agent, and streaming chat services become stateless adapters over
`SessionHarnessFactory` and state-core repositories. They may translate existing Pydantic
schemas but may not dual-write legacy tables. Existing IDs and response fields remain
compatible.

Legacy database data is read only through an explicit migration path. Old Plan managers,
process-local Todo stores, legacy agent registries, and duplicate task state are removed
from the primary runtime. Compatibility modules may remain as thin re-exports or adapters
only when import compatibility requires them.

## Error Handling

All boundaries use typed errors with a stable category, safe message, structured details,
and cause retained for internal logging. Errors are normalized once at the harness boundary.

- Validation failures do not reserve execution budget.
- Hook and permission failures produce durable tool results.
- Timeout and cancellation are distinct.
- Serialization failures identify the failing JSON path.
- MCP transport errors identify the server but redact credentials and headers.
- Worktree destructive actions fail closed.
- Cleanup failures are recorded without replacing an earlier, more specific termination
  reason.

## Delivery Stages And Gates

### Stage 1: Harness And Agent Scheduler

Deliver SessionHarness, durable agent repository, scheduler, Agent background input,
TaskOutput/TaskStop-compatible tools, child scopes, reconciliation, and lifecycle tests.

Gate: foreground/background, wait/status/stop, parent-child scope, concurrency, cancellation,
and process recovery tests all pass.

### Stage 2: Tool Pipeline, Hooks, And Skills

Deliver the ordered pipeline, executable hooks, progressive skill resolution, safe resource
paths, snapshots, and child-agent inheritance.

Gate: input mutation, blocking, failure policies, skill prompt/tool isolation, version
stability, timeout, cancellation, and transcript tests all pass.

### Stage 3: Context, Budget, And Observability

Deliver QueryEngine compaction integration, compact recovery, hierarchical budgets, spans,
usage summaries, and termination aggregation.

Gate: compaction thresholds and recovery plus every budget dimension and concurrent
reservation behavior pass.

### Stage 4: MCP

Deliver real transports, discovery, calls, resources, reconnect, auth status, child scope,
and shutdown.

Gate: tests against real stdio and HTTP test servers pass without mock tool results.

### Stage 5: Worktree And ToolSearch

Deliver explicit cwd routing, durable ownership, safe create/restore/keep/remove, deferred
schema activation, and resume.

Gate: temporary Git repository tests and deferred activation/recovery tests pass.

### Stage 6: API Compatibility And Cleanup

Replace service implementations with adapters, migrate legacy reads, remove dual writes and
primary legacy managers, and add HTTP recovery tests.

Gate: a newly constructed runtime factory observes every API mutation, streaming and
non-streaming tools preserve exact history, and the full suite passes.

## Verification

Each stage follows test-driven development: add a failing behavior test, confirm the expected
failure, implement the smallest coherent runtime behavior, and run focused plus affected
regression suites.

Final verification requires:

```bash
.venv/bin/pytest -q
.venv/bin/python -m compileall -q state_core harness agents plan services tools query_engine.py tests
.venv/bin/ruff check --select E9,F state_core harness agents plan services tools query_engine.py tests
git diff --check
```

Additional acceptance tests exercise:

- cold resume with an interrupted background agent;
- foreground and background nested agent scopes;
- compact boundary recovery;
- concurrent budget reservation;
- real stdio and HTTP MCP servers;
- worktree recovery and fail-closed removal in a temporary repository;
- deferred tool activation across resume; and
- HTTP mutations recovered through a new runtime factory.

## Non-Goals

This delivery does not implement the Node UI, remote hosted agents, tmux teammate teams,
product feature gates, marketplace UI, proprietary analytics exporters, or automatic replay
of interrupted mutations. It does not promise byte-for-byte Node output; it promises the
same core lifecycle, safety, persistence, and tool orchestration semantics.

## Completion Criteria

The project is complete for this scope when:

1. all six delivery gates pass;
2. state-core is the only mutable source of truth;
3. no primary path uses a process-local Plan, Todo, Agent, hook, worktree, or task manager;
4. every tool and subagent call crosses the controlled harness pipeline;
5. resume reconstructs stable context without replaying mutations;
6. MCP and worktree tests use real resources and safe cleanup;
7. current public HTTP and tool contracts remain compatible; and
8. the full verification suite passes before the final commit and push.
