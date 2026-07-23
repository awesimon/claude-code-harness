# P0 Harness Runtime Design

## Goal

Turn the existing collection of agent and tool modules into one dependable execution path. P0 covers tool contracts, a shared tool runtime, centralized permissions, cancellation and timeouts, and a correct foreground/background subagent lifecycle. Plan, Tasks, Skills, hooks, compaction, persistence, and worktree isolation remain P1/P2 except where compatibility is required.

## Constraints

- Preserve existing public tool names through aliases while adopting lower snake case as the canonical internal naming convention.
- Preserve current user changes in configuration paths, skill loading, and tool exports.
- Do not require a database migration.
- Do not make network calls in tests.
- Keep the worker-pool package importable, but stop exposing its `agent` tool through the primary harness.

## Considered Approaches

### Rewrite every tool and agent

This gives the cleanest end state but changes dozens of files at once and makes regressions difficult to localize. It is too broad for P0.

### Add adapters around both existing runtimes

This minimizes edits but preserves two sources of truth for state, permissions, cancellation, and tool execution. It does not produce a controllable harness.

### Compatibility normalization with one primary runtime

This is the selected approach. A shared runtime normalizes existing schemas and names at the boundary, runs validation and permission checks in one place, and is used by both `QueryEngine` and `AgentExecutor`. The root `agents.engine` runtime becomes authoritative; `agents.worker_pool` remains available as a lower-level package but its duplicate `agent` tool is not registered in the primary tool catalog.

## Architecture

### Tool contract

`ToolSpec` is the single LLM-facing representation. It contains a canonical name, description, JSON Schema parameters, aliases, and execution traits. Schema normalization accepts existing `parameters`, `input_schema`, `inputSchema`, class-level `input_schema`, and `get_input_schema()` forms. Invalid or missing schemas fail closed to an empty object schema with `additionalProperties: false` only when the tool genuinely has no declared input.

`ToolResult` retains the `error` data field. Factories are `ok()` and `fail()`; all production call sites migrate away from the conflicting `error()` classmethod and obsolete `success()` method.

### Name resolution

Canonical tool names use lower snake case. Explicit compatibility aliases cover Claude-style names such as `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash`, `Agent`, `EnterPlanMode`, `ExitPlanMode`, and `AskUserQuestion`. `ToolRegistry.resolve_name()` and `ToolRegistry.get()` resolve both canonical names and aliases without registering duplicate tool instances.

### Tool runtime

`ToolRuntime` owns the complete call lifecycle:

1. Resolve the requested tool and canonical name.
2. Validate input through the existing `Tool.run()` contract.
3. Ask `PermissionPolicy` for allow, deny, or ask.
4. Execute with timeout and cancellation propagation.
5. Return a structured `ToolExecution` with a `ToolResult` and termination reason.

Parallel calls are permitted only for read-only tools. Mutating calls execute sequentially so tool implementations do not race on shared state.

### Permissions

`PermissionPolicy` receives a `RuntimeContext` containing session ID, workspace root, permission mode, optional approval callback, and parent cancellation token. It enforces workspace boundaries for file path arguments, plan-mode read-only restrictions, destructive-tool confirmation, and a fail-closed rule when approval is required but no callback is available. Bypass mode is explicit and inherited only when passed in the child context.

### Cancellation and limits

`CancellationToken` is shared from a parent run to all child tool and agent operations. Cancellation interrupts tracked `asyncio.Task` instances. Each tool call has a configurable timeout. Agent runs have max turns and produce a structured termination reason: `completed`, `max_turns`, `cancelled`, `timeout`, or `failed`.

### Agent lifecycle

`SpawnAgentManager` stores the execution task, completion future, result, and status for every child. Foreground spawn executes and returns a result; background spawn returns an ID immediately. `wait_for_agent()` waits with an optional timeout, and `abort_agent()` cancels the actual task. The assistant message passed back to the LLM includes its original `tool_calls`, preserving OpenAI/Anthropic-compatible ordering.

## Error Handling

- Unknown tools return a failed `ToolResult` without raising out of the harness.
- Schema and input validation failures are observable tool failures.
- Permission denial has a distinct termination reason and message.
- Cancellation is not reported as successful completion.
- Unexpected tool exceptions are converted to `ToolExecutionError`; unexpected agent failures set agent status to `failed` and remain available through status/wait APIs.

## Testing

Tests use the standard library `unittest` runner so they work in the current virtual environment without downloading the optional pytest dependency. Contract tests cover all registered schemas, alias resolution, `ToolResult`, permissions, workspace boundaries, timeout/cancellation, LLM tool-call message ordering, and foreground/background subagent lifecycle. Existing tests are run when their dependencies are available.

## Out of Scope

- Plan approval UI and persistent Plan state unification
- Task/Todo database unification
- Skill lifecycle injection into agents
- Hooks execution, compaction, transcript resume, cost budgets, MCP lifecycle, and worktree isolation
