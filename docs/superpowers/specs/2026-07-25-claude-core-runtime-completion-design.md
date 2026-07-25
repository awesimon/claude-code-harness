# Claude Core Runtime Completion Design

**Status:** Proposed for implementation planning

**Date:** 2026-07-25

## Goal

Complete five non-UI Claude Code runtime capabilities on top of the existing
durable Python harness:

1. durable permission approval;
2. complete hook lifecycle dispatch;
3. background shell tasks;
4. automatic skill discovery and explicit activation; and
5. durable Agent Teams.

The Node implementation under `/Users/simon/github/claude-code/src` is the
behavioral reference. The Python implementation keeps its stronger durable
state and recovery rules where the Node implementation relies on process-local
state.

## Scope

### Included

- API-neutral domain services and durable repositories;
- main-session and child-agent integration;
- foreground and background execution behavior;
- restart reconciliation without replaying external side effects;
- cancellation, timeout, revision, ownership, and idempotency rules;
- compatibility adapters for existing Python public tools; and
- focused, integration, recovery, concurrency, and full-suite tests.

### Excluded

- React/Ink/TUI views, keyboard shortcuts, terminal panes, tmux, and iTerm;
- SaaS users, tenants, billing, RBAC, and deployment topology;
- Claude.ai/CCR remote bridges and proprietary notification channels;
- a new `/clear` implementation;
- semantic/vector skill ranking, because the reference checkout contains only
  experimental placeholders for it;
- `MonitorTool`, whose reference implementation is currently null;
- generic research-run orchestration, citation storage, and distributed
  scheduling; and
- automatic replay of model, tool, hook, shell, or teammate side effects after
  a process crash.

## Design Principles

1. `SessionHarness` remains the composition root. No subsystem may introduce a
   second process-global manager.
2. Durable records contain identity, state, revisions, safe inputs, and results.
   Live futures, subprocess handles, and cancellation primitives remain local.
3. Every state transition uses compare-and-set revision checks. A terminal
   transition cannot be overwritten by a late completion, approval, or stop.
4. External side effects are never replayed automatically. An interrupted side
   effect remains interrupted unless an explicit caller creates a new attempt;
   the original attempt and its idempotency key remain immutable for audit.
5. Tool, hook, approval, agent, team, and shell events share correlation IDs and
   the append-only session transcript.
6. Existing `Task V2` work items remain separate from executable background
   tasks. Shared names such as `TaskOutput` are compatibility dispatch surfaces,
   not evidence that the records have the same domain meaning.

## Architecture

### Durable Runtime Records

Add dedicated records instead of storing critical state in generic metadata.

#### PermissionRequestRecord

Fields:

- `request_id`, `root_session_id`, `agent_id`, `tool_call_id`;
- canonical tool name, original input, effective input, and input digest;
- reason, permission mode, suggestions, and policy revision;
- status, revision, created/deadline/resolved timestamps;
- actor, decision reason, updated input, and permission updates; and
- cancellation/interruption reason and idempotency key.

State machine:

```text
pending -> approved | denied | timed_out | cancelled | interrupted | superseded
```

Repeating the same terminal decision is idempotent. A conflicting decision or a
late decision after timeout/cancellation fails with a revision conflict.

#### ApprovedToolExecutionRecord

An approved request may have at most one explicit post-restart execution record.
It stores the request and policy revisions, claim owner, tool call correlation,
status, timestamps, result reference, sanitized failure, and idempotency key.

```text
pending -> running -> succeeded | failed | cancelled | interrupted
```

Creating this record is the atomic claim that prevents two resume callers from
executing the same approval. A crash after tool dispatch leaves the execution
`interrupted`; it is never retried automatically because the external side
effect may already have occurred.

#### PermissionRuleRecord

Stores normalized allow/deny/ask rules, directories, permission mode changes,
scope, source, revision, and creation/revocation timestamps. The core supports
the Node update operations `addRules`, `replaceRules`, `removeRules`, `setMode`,
`addDirectories`, and `removeDirectories`. The scope enum preserves the Node
destinations `userSettings`, `projectSettings`, `localSettings`, `session`, and
`cliArg`. The first three are repository-backed durable scopes; `session` and
`cliArg` are restored only as part of their owning runtime/session snapshot.

#### HookDefinitionRecord And HookInvocationRecord

Hook definitions include stable ID, event, matcher, runner kind/config, source,
order, timeout, `once`, async mode, enabled state, and immutable config revision.

Each invocation records its event envelope, definition revision, correlation
IDs, lease, attempt, deadline, status, structured outcome, sanitized failure,
duration, and idempotency key.

Invocation state machine:

```text
queued -> running -> succeeded | blocked | failed | timed_out | cancelled | interrupted
queued -> cancelled | timed_out
```

Interrupted external hooks are not automatically replayed. A hook explicitly
declared idempotent may be retried only as a new invocation that references the
original attempt.

#### ExecutionTaskRecord

The only executable task kind in this design is `shell`. Fields:

- task/root/agent IDs, kind, command, description, canonical cwd;
- status, revision, timestamps, exit code, and termination reason;
- output artifact identity, output byte count, and last readable cursor; and
- timeout, process owner token, and safe environment snapshot.

State machine:

```text
pending -> running
running -> completed | failed | killed | timed_out | interrupted
pending -> failed | killed | timed_out
```

#### Team Records

`TeamRecord` stores team identity, root session, lead agent, shared Task V2 list,
status, revision, and timestamps.

```text
active -> closing -> closed
```

`TeamMemberRecord` stores agent/name/type, role, status, assigned task IDs,
mailbox cursor, shutdown request, and revision.

`TeamMessageRecord` is an append-only mailbox entry with sender, recipient or
broadcast target, message type, structured body, request correlation ID,
delivery state, and sequence.

Team member states:

```text
starting -> running -> idle -> running
starting | running | idle -> shutdown_requested -> stopped
starting | running | idle | shutdown_requested -> failed | interrupted
interrupted -> starting
```

## Unified Lifecycle Dispatcher

Introduce a `LifecycleDispatcher` that builds one normalized event envelope and
delegates to `HookDispatcher`. Producers emit domain events; they do not know
about command, prompt, HTTP, or agent hook runners.

Every envelope includes:

- root session, agent, agent type, event, timestamp, and correlation ID;
- canonical cwd, permission mode, transcript position;
- optional tool call, task, team, worktree, MCP, or config identifiers; and
- an event-specific JSON payload passed through size and secret allowlists.

The dispatcher supports the complete Node hook event set already declared by
`HookEvent`:

- tool: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`;
- permission: `PermissionRequest`, `PermissionDenied`;
- turn/session: `UserPromptSubmit`, `SessionStart`, `SessionEnd`, `Stop`,
  `StopFailure`, `Notification`, `Setup`;
- agent/team/task: `SubagentStart`, `SubagentStop`, `TeammateIdle`,
  `TaskCreated`, `TaskCompleted`;
- context: `PreCompact`, `PostCompact`;
- MCP: `Elicitation`, `ElicitationResult`;
- configuration: `ConfigChange`, `InstructionsLoaded`, `CwdChanged`,
  `FileChanged`; and
- isolation: `WorktreeCreate`, `WorktreeRemove`.

Events without an existing Python producer receive a narrow producer interface
in the relevant service. This work does not require a UI or SaaS adapter.

Matching hooks for one event run concurrently with independent cancellation and
timeouts. Results are aggregated in stable definition order. Any blocking result
blocks the event. Conflicting input patches are applied in definition order,
with later definitions winning at the individual key. Permission denial wins
over approval. `once` is consumed atomically when the invocation is claimed.

Supported runner kinds are:

- `command`: existing bounded subprocess protocol;
- `prompt`: a harness-injected model call with hook budget and recursion guard;
- `http`: bounded JSON request/response using an injected transport; and
- `agent`: a foreground child-agent call using a restricted hook scope.

Async hooks create durable invocations and return immediately. `asyncRewake`
emits a durable notification when complete; it does not silently restart a
finished query loop.

`Stop`, `TaskCompleted`, and `TeammateIdle` may return blocking feedback to the
model. A durable attempt counter prevents infinite stop-hook loops.

## Durable Permission Approval

Refactor the boolean approval callback into domain outcomes:

```text
Allow(effective_input, permission_updates)
Deny(reason, interrupt)
ApprovalRequired(request_id, deadline)
```

Tool authorization order remains:

```text
resolve tool
-> deferred activation check
-> schema validation
-> PreToolUse hooks
-> revalidate modified input and workspace boundary
-> permission rules
-> PermissionRequest hooks
-> durable approval when still undecided
-> execute
-> PostToolUse or PostToolUseFailure
```

A `PermissionRequest` hook can allow, deny, modify input, or propose permission
updates. Headless execution without an approval adapter runs this hook and then
fails closed.

`ApprovalService` provides API-neutral methods to create, list, await, resolve,
cancel, and reconcile requests. Approval resolution and permission rule updates
commit atomically with an outbox event.

In a live process, the tool task may await the durable request. If the process
restarts, the request remains visible but the original coroutine is not
recreated. An unexpired `pending` request remains resolvable after its waiter
ownership is detached. Once approved, an explicit
`resume_approved_tool(request_id, expected_revision)` creates the unique
`ApprovedToolExecutionRecord` and executes the canonical tool call under that
claim. Expired requests become `timed_out`; requests missing the canonical input
or policy binding needed for safe resume become `interrupted`. No request
executes merely because the service restarted or because an approval decision
was persisted.

## Background Shell Tasks

Add `run_in_background` to Bash. Foreground behavior remains unchanged.

Background launch flow:

1. validate and authorize the Bash call through the normal tool pipeline;
2. persist a `pending` `ExecutionTaskRecord` before process creation;
3. start the subprocess in its own process group using the harness cwd;
4. publish the revision-checked `running` transition and then return its task ID;
5. append stdout/stderr to a bounded output artifact;
6. flush output before publishing a terminal transition; and
7. emit lifecycle notifications and hook events.

Process creation failure transitions the durable record to `failed` and returns
a normal tool failure. Cancellation before the `running` result is returned
terminates the process group. After return, the background task is detached from
the originating tool call and is controlled by `TaskStop`, its own timeout, or
runtime shutdown reconciliation.

`TaskOutput` and `TaskStop` become kind-aware dispatchers. Existing agent task
behavior remains compatible. Shell output supports blocking and nonblocking
reads, byte cursors, bounded tail reads, timeout, and terminal reads.

Natural exit, timeout, cancellation, and stop race through one revision-checked
terminal transition. Stop terminates the complete process group. On restart,
nonterminal shell tasks become `interrupted`; their existing output remains
readable and no PID resurrection is attempted.

## Skill Discovery And Activation

The reference behavior is metadata discovery followed by explicit model
invocation, not automatic semantic selection.

`SkillCatalog` discovers valid skill metadata from configured roots and nested
directories relevant to the current cwd or accessed paths. Deeper valid skill
definitions override shallower duplicates. Discovery validates containment and
frontmatter but does not load bodies or cause side effects.

Each model-calling agent, including the lead and child agents, tracks announced
`(name, digest)` pairs. Eligible skills are announced once on that agent's first
model turn; later additions are sent as deltas. Listing size is capped at one
percent of the context budget with an 8,000-character default ceiling.

`skill_execute` remains explicit. Activation performs one idempotent operation:

1. resolve the immutable skill snapshot;
2. verify required MCP servers and allowed tools;
3. persist activation identity and digest;
4. register skill hooks once;
5. apply tool visibility and permission deltas; and
6. inject linked skill messages before the next model request.

Missing required MCP servers fail before any injection or registration. Resume
and compaction recover announcement and activation state without duplicating
skill bodies or hook registrations. Static skills declared by an agent use the
same activation path.

## Agent Teams

Replace the current legacy SQL/file team tools with adapters over durable team
repositories and the existing `AgentScheduler` and Task V2 runtime.

Core tool surface:

- `TeamCreate` creates one team and one shared Task V2 list;
- `Agent` accepts Node-compatible `team_name`, `name`, and teammate mode fields;
- `SendMessage` writes direct or broadcast mailbox messages;
- `TeamDelete` closes a team only after all members are terminal; and
- existing Task tools operate on the team's shared list.

Only the lead can create/delete the team or request teammate shutdown. A
teammate cannot spawn another teammate. Teammates inherit the root harness but
receive agent-scoped cancellation, mailbox cursor, tool visibility, permissions,
budgets, skills, MCP configuration, and optional worktree.

Unlike one-shot subagents, a teammate completes a response and then becomes
`idle`, waiting for mailbox input or a shutdown request. Shutdown uses correlated
`shutdown_request` and `shutdown_response` messages. Plan approval messages use
the same durable mailbox but remain distinct from user tool permissions.

Task creation and completion emit hooks. Before entering idle, the teammate runs
`TaskCompleted`, `SubagentStop`, and `TeammateIdle` gates as applicable. Blocking
feedback resumes the teammate loop instead of publishing idle.

Mail delivery is append-only and cursor based. Direct and broadcast messages are
idempotent by message ID. A crash marks owned live teammates interrupted. An
explicit resume reconstructs their transcript and mailbox cursor; the harness
does not automatically replay their last model or tool call.

The implementation includes only the in-process backend. Tmux, iTerm, pane
tracking, CLI environment propagation, colors, and terminal rendering are out of
scope.

## Recovery And Ownership

`SessionHarnessFactory.resume()` performs one ordered reconciliation:

1. detach orphaned permission waiters, expire deadlines, and preserve safe
   unexpired requests for later resolution;
2. interrupt abandoned hook invocations;
3. interrupt nonterminal shell tasks without a live process owner;
4. reconcile subagents and teammates by owner token;
5. restore worktree validation;
6. restore skill announcements and activations; and
7. expose durable pending approvals, messages, output, and terminal records.

Observer factories remain read-only and cannot steal scheduler, shell, hook, or
team ownership.

## Error And Security Rules

- Persist only allowlisted payload fields and sanitized failures.
- Never store credentials, authorization headers, complete process environments,
  or unrestricted hook stdin/stdout.
- All filesystem paths use canonical harness cwd and containment checks.
- Hooks and shell tasks terminate process groups on cancellation or timeout.
- Background output has per-task and per-read limits and safe UTF-8 boundaries.
- Permission approval binds to canonical tool name, effective input digest,
  policy revision, and tool call ID.
- A changed tool input or policy after approval supersedes the request.
- Prompt and agent hooks consume explicit budgets and cannot recursively trigger
  the same hook event.

## Delivery Sequence And Parallelism

### Gate 1: Shared Durable Primitives

Add records, repositories, SQL migrations, status transitions, correlation IDs,
leases, output storage, and recovery contracts. This gate is sequential because
all later work depends on stable storage contracts.

### Parallel Lane A: Hooks And Permission Approval

Build the lifecycle dispatcher, hook runners/effect aggregation, all event
producers, durable approval state machine, permission rule updates, and explicit
resume of approved tool calls.

### Parallel Lane B: Background Shell

Build execution-task storage, output cursors, process-group lifecycle, background
Bash, and kind-aware `TaskOutput`/`TaskStop`.

### Parallel Lane C: Skill Discovery

Build scoped catalog discovery, announcement deltas, bounded prompt injection,
atomic activation, required MCP enforcement, and recovery integration.

The three lanes may run in parallel after Gate 1 because they own distinct
modules and tests. Lane A defines shared hook/approval contracts but the other
lanes consume them only during integration.

### Gate 2: Agent Teams

Build teams after Lane A contracts are stable. Teams depend on AgentScheduler,
Task V2, mailbox records, lifecycle hooks, and permission behavior.

### Gate 3: Integration And Acceptance

Connect cross-lane events, remove legacy primary team ownership, update public
contracts, run recovery/concurrency acceptance, then run the full suite and
static checks before commit and push.

## Acceptance Criteria

### Permission Approval

- Approval requests and rule updates survive a new runtime factory.
- An approval resolved after restart requires one explicit, uniquely claimed
  execution; concurrent resume callers cannot both dispatch the tool.
- Approve/deny/timeout/cancel/interrupted/superseded transitions are covered.
- Concurrent conflicting decisions have one revision-checked winner.
- Late approvals cannot execute cancelled or changed tool calls.
- Permission hooks can allow, deny, modify input, and return rule updates.
- Headless execution fails closed after hooks when no adapter can decide.

### Hooks

- Every declared HookEvent has a real producer and payload contract test.
- Matching hooks run concurrently and aggregate deterministically.
- Command, prompt, HTTP, and agent runners honor timeout, cancellation, output
  limits, recursion guards, `once`, and async behavior.
- Stop/TaskCompleted/TeammateIdle blocking feedback re-enters the model with an
  attempt limit.
- Interrupted external hooks are not silently replayed.

### Background Shell

- Background Bash returns only after the running record is durable.
- Cancellation before launch completion kills the process, while cancellation
  of the completed tool call does not kill the detached background task.
- Cursor reads produce no missing or duplicated bytes.
- Exit zero, nonzero, timeout, stop, cancellation, and crash interruption are
  distinguishable.
- Output is flushed before terminal status and remains readable after resume.
- Existing agent TaskOutput/TaskStop contracts continue to pass.

### Skills

- Discovery loads metadata only and causes no hook, permission, or MCP effects.
- Each agent receives a bounded initial listing and later digest deltas.
- Explicit activation injects one immutable snapshot exactly once.
- Required MCP, allowed tools, hooks, and visibility changes apply atomically.
- Nested precedence, containment, resume, and compaction are covered.

### Agent Teams

- Team create/delete, teammate spawn, shared Task V2, direct/broadcast mailbox,
  idle, shutdown, failure, cancellation, and explicit resume are durable.
- Teammates cannot spawn teammates or mutate another team's state.
- Mailbox cursor and task transitions are race-safe and idempotent.
- Task, subagent, idle, permission, and stop hooks execute in the documented
  order.
- No TUI, pane, or product-specific dependency enters the core services.

### Final Gate

- Focused suites pass for every lane.
- Cold recovery acceptance covers pending approval, running hook, background
  shell, activated skill, and active team states.
- `pytest`, `compileall`, Ruff E9/F, and `git diff --check` pass.
- Tracked databases and generated output artifacts are absent from the commit.

## Reference Boundaries

Primary Node references:

- permissions: `src/utils/permissions`, `src/hooks/toolPermission`;
- hooks: `src/utils/hooks.ts`, `src/query/stopHooks.ts`, `src/types/hooks.ts`;
- shell tasks: `src/tasks/LocalShellTask`, `src/utils/task`,
  `src/tools/BashTool`, `TaskOutputTool`, and `TaskStopTool`;
- skills: `src/skills/loadSkillsDir.ts`, `src/tools/SkillTool`, and
  `src/utils/attachments.ts`; and
- teams: `src/tools/TeamCreateTool`, `src/tools/SendMessageTool`,
  `src/tools/shared/spawnMultiAgent.ts`, and `src/utils/swarm`.

The Python design intentionally follows these behavioral contracts without
copying terminal presentation or proprietary remote integrations.
