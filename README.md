# Claude Code Python API

A Python FastAPI implementation of Claude Code core features, providing AI-powered coding assistance with advanced tool capabilities, multi-provider LLM support, and production-ready error handling.

## Overview

This project implements a Python-based alternative to Claude Code, featuring a FastAPI backend with a React frontend. It supports multiple LLM providers and includes advanced features like context compaction, error recovery, and agent-based task execution.

## Key Features

### Core Capabilities

- **Advanced Tool System**: 37+ built-in tools including file operations (read/write/edit), code search (Glob/Grep), Bash command execution, web search, and more
- **Agent Management**: Create and manage AI agents with specific capabilities, task assignment, and parallel execution
- **Multi-Provider LLM Support**: OpenAI, Anthropic, DeepSeek, GLM, MiniMax, and Moonshot AI (Kimi)
- **Streaming Responses**: Server-Sent Events (SSE) for real-time streaming chat completions
- **Plan Mode**: Structured implementation planning with approval workflow

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
├── query_engine.py         # Core conversation engine with error recovery
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
├── agent/                 # Agent management system
│   ├── __init__.py
│   └── agent_manager.py
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

Run the test suite:

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=services --cov-report=html

# Run specific test file
pytest tests/test_context_compaction.py -v
pytest tests/test_error_recovery.py -v
```

Current test coverage:
- Context Compaction: 42 tests ✅
- Error Recovery: 36 tests ✅
- Total: 78 tests passing

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
