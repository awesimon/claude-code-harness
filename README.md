# Claude Code Python API

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

A Python FastAPI implementation of Claude Code core features, supporting multiple LLM providers (OpenAI, Anthropic, DeepSeek, GLM, MiniMax, Kimi).

### Features

- **Tools API**: File operations (read/write/edit), code search (Glob/Grep), Bash command execution
- **Agent Management**: Create/manage agents, task assignment, parallel execution
- **Task Scheduling**: Priority queue, async execution, status tracking
- **Team Management**: Create/delete teams, multi-agent collaboration
- **Plan Mode**: Support for plan mode enter/exit
- **MCP Support**: MCP server management, tool execution, resource access
- **Cron Jobs**: Task scheduling with Cron expressions
- **LLM Integration**: Support for multiple providers (OpenAI, Anthropic, DeepSeek, GLM, MiniMax, Kimi)
- **Streaming**: SSE streaming response support
- **Model Management**: 12 built-in models with dynamic configuration

### Built-in Models

- **OpenAI**: gpt-4o, gpt-4o-mini
- **Anthropic**: claude-3-5-sonnet, claude-3-opus
- **DeepSeek**: deepseek-v3.2, deepseek-v3.2-thinking
- **GLM**: glm-4.7, glm-5
- **MiniMax**: minimax-m2.1, minimax-m2.5, minimax-m2.7
- **Kimi**: kimi-k2.5

### Quick Start

```bash
cd python_api

# Install uv
pip install uv

# Create virtual environment
uv venv
source .venv/bin/activate

# Install dependencies
uv pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Start backend
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Start frontend (in another terminal)
cd frontend
npm install
npm run dev
```

### API Endpoints

- **Tools**: `/tools/read-file`, `/tools/write-file`, `/tools/edit-file`, `/tools/glob`, `/tools/grep`, `/tools/bash`
- **Agents**: `/agents` (POST, GET, DELETE)
- **Tasks**: `/tasks` (POST, GET)
- **Teams**: `/teams` (POST, DELETE)
- **Models**: `/api/models` (GET, POST)
- **Chat**: `/chat/create`, `/chat`, `/chat/stream`
- **LLM**: `/llm/chat`, `/llm/chat/stream`, `/llm/models`
- **System**: `/stats`, `/health`

### Project Structure

```
python_api/
├── main.py              # FastAPI main app
├── query_engine.py      # Core conversation engine
├── config/              # Configuration module
├── routers/             # API routers
├── tools/               # 37+ tools
├── agent/               # Agent module
├── services/            # Services (LLM, config)
└── frontend/            # React frontend
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key | - |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |
| `DEFAULT_MODEL` | Default model | gpt-4o |
| `HOST` | Server host | 0.0.0.0 |
| `PORT` | Server port | 8000 |

---

<a name="中文"></a>
## 中文

将Claude Code核心功能改造成Python FastAPI服务端接口，支持多提供商LLM调用。

### 功能特性

- **工具API**: 文件操作（读/写/编辑）、代码搜索（Glob/Grep）、Bash命令执行
- **Agent管理**: 创建/管理Agent、任务分配、并行执行
- **任务调度**: 优先级队列、异步执行、状态跟踪
- **团队管理**: 创建/删除团队，多Agent协作
- **计划模式**: 支持计划模式进入/退出
- **MCP支持**: MCP服务器管理、工具执行、资源访问
- **定时任务**: Cron表达式支持的任务调度
- **LLM集成**: 支持多提供商（OpenAI、Anthropic、DeepSeek、GLM、MiniMax、Kimi）
- **流式响应**: 支持SSE流式输出
- **模型管理**: 内置12个模型，支持动态配置

### 内置模型

- **OpenAI**: gpt-4o, gpt-4o-mini
- **Anthropic**: claude-3-5-sonnet, claude-3-opus
- **DeepSeek**: deepseek-v3.2, deepseek-v3.2-thinking
- **GLM**: glm-4.7, glm-5
- **MiniMax**: minimax-m2.1, minimax-m2.5, minimax-m2.7
- **Kimi**: kimi-k2.5

### 快速开始

```bash
cd python_api

# 安装 uv
pip install uv

# 创建虚拟环境
uv venv
source .venv/bin/activate

# 安装依赖
uv pip install -e ".[dev]"

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入API密钥

# 启动后端
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 启动前端（另一个终端）
cd frontend
npm install
npm run dev
```

### API端点

- **工具**: `/tools/read-file`, `/tools/write-file`, `/tools/edit-file`, `/tools/glob`, `/tools/grep`, `/tools/bash`
- **Agent**: `/agents` (POST, GET, DELETE)
- **任务**: `/tasks` (POST, GET)
- **团队**: `/teams` (POST, DELETE)
- **模型**: `/api/models` (GET, POST)
- **对话**: `/chat/create`, `/chat`, `/chat/stream`
- **LLM**: `/llm/chat`, `/llm/chat/stream`, `/llm/models`
- **系统**: `/stats`, `/health`

### 项目结构

```
python_api/
├── main.py              # FastAPI主应用
├── query_engine.py      # 核心对话引擎
├── config/              # 配置模块
├── routers/             # API路由
├── tools/               # 37+个工具
├── agent/               # Agent模块
├── services/            # 服务模块
└── frontend/            # React前端
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | OpenAI API密钥 | - |
| `ANTHROPIC_API_KEY` | Anthropic API密钥 | - |
| `DEFAULT_MODEL` | 默认模型 | gpt-4o |
| `HOST` | 服务主机 | 0.0.0.0 |
| `PORT` | 服务端口 | 8000 |

---

## License

仅供学习研究使用，源码版权归Anthropic所有。
For educational and research purposes only. Source code copyright belongs to Anthropic.
