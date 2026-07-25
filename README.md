# Claude Code Python API

A Python FastAPI implementation of Claude Code core features, providing AI-powered coding assistance with advanced tool capabilities, multi-provider LLM support, and production-ready error handling.

## Overview

This project implements a Python-based alternative to Claude Code, featuring a FastAPI backend with a React frontend. It supports multiple LLM providers and includes advanced features like context compaction, error recovery, and agent-based task execution.

## Key Features

### Core Capabilities

- **Advanced Tool System**: 37+ built-in tools including file operations (read/write/edit), code search (Glob/Grep), Bash command execution, web search, and more
- **Durable Agent Scheduling**: Spawn foreground or background subagents with persisted lifecycle, cancellation, limits, and restart reconciliation
- **Multi-Provider LLM Support**: OpenAI, Anthropic, DeepSeek, GLM, MiniMax, and Moonshot AI (Kimi)
- **Streaming Responses**: Server-Sent Events (SSE) for real-time streaming chat completions
- **Plan Mode**: Structured implementation planning with approval workflow
- **Durable Agent Harness**: Session-scoped runtime with append-only transcripts,
  snapshots, restart recovery, Task V2/Todo compatibility modes, and interrupted
  subagent detection

### Durable Session State

`state_core.SessionRuntime` is the single authority for plan state, task lists,
TodoWrite compatibility data, transcript events, and agent lifecycle state.
Task V2 and TodoWrite are mutually exclusive per session. Restart recovery loads
the latest valid state, marks active work as interrupted, and never replays a
mutating tool call.

### Durable Harness Runtime

`harness.SessionHarnessFactory` is the composition root used by `QueryEngine`,
subagents, tools, and compatibility APIs. A root harness owns one durable
session runtime, while child harnesses inherit the root store, permissions,
budgets, MCP definitions, skills, hooks, and effective working directory with
agent-scoped cancellation and activation state.

- **Agents**: `Agent` supports foreground and background execution.
  `TaskOutput` waits for or inspects durable results, and `TaskStop` requests
  cancellation. Parent/child ownership, concurrency limits, usage, errors, and
  terminal reasons are persisted before callers are released.
- **Task V2 and TodoWrite**: Task V2 provides durable dependency, ownership,
  claim, update, and completion semantics. TodoWrite remains available as a
  compatibility mode, but the two modes cannot mutate the same session.
- **Plan mode**: Enter, draft, submit, approve, reject, and exit transitions are
  state-core events. Plan files are durable projections; approval state is not
  inferred from files alone.
- **Skills and hooks**: Skills are indexed progressively, resolved inside
  configured roots, and snapshotted for child agents. Hooks run through the
  controlled tool boundary with matching, timeouts, cancellation, bounded
  output, and durable events.
- **Budgets and tracing**: Model, tool, hook, MCP, and child-agent work reserve
  hierarchical budgets. Durable spans record lifecycle and safe usage/error
  summaries without storing credentials.
- **Context control**: Streaming and non-streaming model turns share the same
  compaction controller. Compaction writes a validated boundary and summary
  while preserving the raw append-only transcript.
- **MCP**: Session-scoped stdio and streamable HTTP clients perform real tool,
  resource, and prompt discovery. Child definitions are additive, transports
  honor timeout/cancellation, and disconnect removes live schemas.
- **Deferred tools**: Deferred schemas are hidden until `ToolSearch` activates
  them. `select:<tool-name>` performs exact activation; keyword discovery and
  activation history are scoped per root or child and survive resume.
- **Worktrees**: Harness-owned worktrees use explicit `git -C` and effective
  cwd routing. The runtime never changes process-global cwd, validates durable
  ownership on resume, and fails closed when cleanup would discard unapproved
  changes.

Recovery reconstructs context from snapshots, events, metadata, plan files,
and durable runtime records. In-flight agents become interrupted when no live
owner exists. External model calls, tool calls, hooks, MCP calls, and filesystem
mutations are never replayed automatically after a restart.

The HTTP conversation, task, plan, agent, and streaming endpoints are stateless
adapters over this runtime. Legacy SQL rows are read only by the explicit
migration path; new mutations do not dual-write legacy tables. Existing public
IDs and response fields remain available through compatibility projections.

### Production-Ready Features

#### Context Compaction Service
Automatically compresses conversation context when approaching token limits:
- **Token Counter**: Accurate token estimation for mixed Chinese/English content and multimodal inputs
- **Compaction Strategies**: Full compaction, partial compaction, and intelligent summarization
- **Auto-Compaction**: Automatic threshold detection with circuit breaker protection
- **Reactive Compaction**: Handles API errors (413 Payload Too Large) gracefully
- **Boundary Markers**: Tracks compaction points in conversation history

#### Error Recovery System
Comprehensive error handling for robust production use:
- **Error Classification**: Distinguishes recoverable vs non-recoverable errors
- **Max Output Tokens Recovery**: Progressive token limit increases (1.5x/2x/4x/8x)
- **Prompt Too Long Handling**: Truncation and compression strategies
- **Exponential Backoff**: Configurable retry mechanism with jitter
- **Circuit Breaker**: Prevents cascade failures during API outages

#### Performance Optimizations
- Message content limiting (50KB per message, 100KB max accumulation)
- DOM node limiting (max 100 messages stored, 50 visible)
- Optimized React rendering with proper memoization
- Markdown parsing error handling

## Supported Models

### OpenAI
- gpt-4o, gpt-4o-mini

### Anthropic
- claude-3-5-sonnet-20241022
- claude-3-opus-20240229

### DeepSeek
- deepseek-v3.2
- deepseek-v3.2-thinking

### GLM
- glm-4.7
- glm-5

### MiniMax
- minimax-m2.1
- minimax-m2.5
- minimax-m2.7

### Moonshot AI (Kimi)
- kimi-k2.5

## Quick Start

### Prerequisites
- Python 3.10+
- Node.js 18+ (for frontend)
- API keys for desired LLM providers

### Installation

```bash
cd python_api

# Install uv (fast Python package installer)
pip install uv

# Create virtual environment
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
uv pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env with your API keys
```

### Configuration

Create a `.env` file with your API keys:

```env
# Required: At least one LLM provider API key
OPENAI_API_KEY=your_openai_key
ANTHROPIC_API_KEY=your_anthropic_key
DEEPSEEK_API_KEY=your_deepseek_key
GLM_API_KEY=your_glm_key
MINIMAX_API_KEY=your_minimax_key
MOONSHOT_API_KEY=your_moonshot_key

# Optional: Default model selection
DEFAULT_MODEL=gpt-4o
DEFAULT_MAX_TOKENS=4096
DEFAULT_TEMPERATURE=0.7

# Server configuration
HOST=0.0.0.0
PORT=8000
```

### Running the Application

```bash
# Start backend
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Start frontend (in another terminal)
cd frontend
npm install
npm run dev
```

The application will be available at:
- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API Documentation: http://localhost:8000/docs

## API Endpoints

### Tools
- `POST /tools/read-file` - Read file contents
- `POST /tools/write-file` - Write file contents
- `POST /tools/edit-file` - Edit file with string replacement
- `POST /tools/glob` - File pattern matching
- `POST /tools/grep` - Content search with regex
- `POST /tools/bash` - Execute shell commands

### Chat
- `POST /chat/create` - Create new conversation
- `POST /chat/{id}/resume` - Restore a durable conversation after restart
- `POST /chat` - Send message (non-streaming)
- `POST /chat/stream` - Send message (SSE streaming)
- `GET /chat/{id}/history` - Get conversation history
- `DELETE /chat/{id}` - Clear conversation

### Agents
- `POST /agents` - Create agent
- `GET /agents` - List agents
- `GET /agents/{id}` - Get agent details
- `DELETE /agents/{id}` - Remove agent

### LLM
- `POST /llm/chat` - Chat completion
- `POST /llm/chat/stream` - Streaming chat completion
- `GET /llm/models` - List available models
- `GET /llm/config` - Get LLM configuration

### System
- `GET /stats` - System statistics
- `GET /health` - Health check

## Project Structure

```
python_api/
├── main.py                 # FastAPI application entry point
├── query_engine.py         # Harness-driven model and tool loop
├── harness/                # Session composition and controlled execution
│   ├── session.py          # SessionHarness and factory
│   ├── agents.py           # Durable AgentScheduler
│   ├── runtime.py          # Ordered tool pipeline
│   ├── context_control.py  # Compaction and recovery boundaries
│   ├── budget.py           # Hierarchical reservations and accounting
│   ├── tracing.py          # Durable spans
│   ├── mcp.py              # Real MCP transport lifecycle
│   ├── worktrees.py        # Session-owned worktree lifecycle
│   └── deferred_tools.py   # ToolSearch activation state
├── state_core/             # Authoritative events, snapshots, and repositories
├── CHANGELOG.md           # Version history and changes
├── config/                # Configuration management
│   └── settings.py
├── routers/               # API route handlers
│   ├── models_router.py
│   ├── plan_router.py
│   └── agents_router.py
├── tools/                 # Tool implementations (37+ tools)
│   ├── base.py
│   ├── file_tools.py
│   ├── search_tools.py
│   └── ...
├── agents/                # Agent definitions and one-child execution loop
├── plan/                  # Durable plan tools and compatibility adapters
├── services/              # Core services
│   ├── llm_service.py     # LLM provider abstraction
│   ├── config_service.py  # Configuration service
│   ├── compact/           # Context compaction service
│   │   ├── __init__.py
│   │   ├── token_counter.py
│   │   ├── compaction.py
│   │   ├── auto_compact.py
│   │   └── reactive_compact.py
│   └── error_recovery/    # Error recovery system
│       ├── __init__.py
│       ├── error_types.py
│       ├── recovery_manager.py
│       ├── retry_handler.py
│       └── token_recovery.py
├── tests/                 # Test suite
│   ├── test_context_compaction.py
│   └── test_error_recovery.py
└── frontend/              # React TypeScript frontend
    ├── src/
    │   ├── components/    # React components
    │   ├── hooks/         # Custom hooks
    │   ├── stores/        # State management (Zustand)
    │   └── lib/           # Utilities
    └── package.json
```

## Architecture

### Context Compaction Flow

```
Conversation → Token Count → Threshold Check
                                    ↓
                    ┌───────────────┴───────────────┐
                    ↓                               ↓
            Below Threshold                  Above Threshold
                    ↓                               ↓
            Continue Normally              Trigger Compaction
                                                    ↓
                                    Select Strategy (Full/Partial/Summary)
                                                    ↓
                                    Compress History + Add Boundary
                                                    ↓
                                           Continue Conversation
```

### Error Recovery Flow

```
API Call → Error Occurs → Error Classification
                                    ↓
                    ┌───────────────┴───────────────┐
                    ↓                               ↓
            Recoverable Error                Non-Recoverable
                    ↓                               ↓
            Apply Recovery Strategy            Fail Fast
                    ↓
    ┌───────────────┼───────────────┐
    ↓               ↓               ↓
Token Limit    Rate Limit     Server Error
    ↓               ↓               ↓
Increase       Backoff        Retry with
Tokens         + Retry        Fallback
```

## Testing

Run the verification gates from the repository root:

```bash
.venv/bin/pytest -q
.venv/bin/python -m compileall -q state_core harness agents plan services tools query_engine.py tests
.venv/bin/ruff check --select E9,F state_core harness agents plan services tools query_engine.py tests
git diff --check
```

Focused suites cover durable recovery, foreground/background agents, Task V2
and Todo compatibility, plan approval, hooks and skills, budget races, context
compaction, real stdio/HTTP MCP servers, worktree cleanup, deferred activation,
and HTTP mutation recovery through newly constructed factories.

## Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `OPENAI_API_KEY` | OpenAI API key | No* | - |
| `ANTHROPIC_API_KEY` | Anthropic API key | No* | - |
| `DEEPSEEK_API_KEY` | DeepSeek API key | No* | - |
| `GLM_API_KEY` | GLM API key | No* | - |
| `MINIMAX_API_KEY` | MiniMax API key | No* | - |
| `MOONSHOT_API_KEY` | Moonshot AI API key | No* | - |
| `DEFAULT_MODEL` | Default LLM model | No | gpt-4o |
| `DEFAULT_MAX_TOKENS` | Default max tokens | No | 4096 |
| `DEFAULT_TEMPERATURE` | Default temperature | No | 0.7 |
| `HOST` | Server bind address | No | 0.0.0.0 |
| `PORT` | Server port | No | 8000 |

*At least one LLM provider API key is required.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Acknowledgments

This project is an independent Python implementation inspired by Claude Code concepts. The codebase is developed and maintained by the community.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history and detailed changes.
