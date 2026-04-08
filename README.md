# Claude Code Python API

将Claude Code核心功能改造成Python FastAPI服务端接口，支持多提供商LLM调用（OpenAI、Anthropic、DeepSeek、GLM、MiniMax、Kimi）。

## 功能特性

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

## 内置模型

- **OpenAI**: gpt-4o, gpt-4o-mini
- **Anthropic**: claude-3-5-sonnet, claude-3-opus
- **DeepSeek**: deepseek-v3.2, deepseek-v3.2-thinking
- **GLM**: glm-4.7, glm-5
- **MiniMax**: minimax-m2.1, minimax-m2.5, minimax-m2.7
- **Kimi**: kimi-k2.5

## 快速开始

### 使用 uv 安装（推荐）

```bash
cd python_api

# 安装 uv
pip install uv

# 创建虚拟环境并安装依赖
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
uv pip install -e ".[dev]"
```

### 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 文件，填入你的API密钥
```

### 启动服务

```bash
# 启动后端服务
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 或使用 start.sh 脚本
./start.sh
```

### 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端将在 http://localhost:5173 启动

### API文档

启动后访问:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## API端点

### 工具API

- `POST /tools/read-file` - 读取文件
- `POST /tools/write-file` - 写入文件
- `POST /tools/edit-file` - 编辑文件
- `POST /tools/glob` - 文件搜索
- `POST /tools/grep` - 内容搜索
- `POST /tools/bash` - 执行命令
- `GET /tools` - 列出所有工具

### Agent API

- `POST /agents` - 创建Agent
- `GET /agents` - 列出Agent
- `GET /agents/{id}` - 获取Agent详情
- `DELETE /agents/{id}` - 移除Agent

### 任务API

- `POST /tasks` - 创建任务
- `GET /tasks` - 列出任务
- `GET /tasks/{id}` - 获取任务详情

### 团队API

- `POST /teams` - 创建团队
- `DELETE /teams/{id}` - 删除团队

### 模型管理API

- `GET /api/models` - 获取所有模型
- `GET /api/models/{model_id}` - 获取模型详情
- `POST /api/models/{model_id}/select` - 设置默认模型
- `GET /api/models/default` - 获取当前默认模型

### 对话API

- `POST /chat/create` - 创建对话
- `POST /chat` - 发送消息（非流式）
- `POST /chat/stream` - 发送消息（流式SSE）
- `GET /chat/{conversation_id}/history` - 获取对话历史
- `DELETE /chat/{conversation_id}` - 清空对话

### LLM API

- `POST /llm/chat` - 聊天完成
- `POST /llm/chat/stream` - 流式聊天完成
- `GET /llm/models` - 列出支持的模型
- `GET /llm/config` - 获取LLM配置

### 系统API

- `GET /stats` - 系统统计
- `GET /health` - 健康检查

## 示例

### 读取文件

```bash
curl -X POST http://localhost:8000/tools/read-file \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/path/to/file.txt"}'
```

### LLM聊天

```bash
curl -X POST http://localhost:8000/llm/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "你好，请介绍一下自己"}
    ],
    "model": "gpt-4o",
    "provider": "openai"
  }'
```

### 流式聊天

```bash
curl -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "message": "讲个故事",
    "conversation_id": "conv-xxx"
  }'
```

### 创建Agent

```bash
curl -X POST http://localhost:8000/agents \
  -H "Content-Type: application/json" \
  -d '{"name": "Worker-1", "capabilities": ["bash", "file"]}'
```

## 项目结构

```
python_api/
├── main.py                 # FastAPI主应用
├── query_engine.py         # 核心对话引擎
├── pyproject.toml          # uv项目配置
├── .env.example            # 环境变量示例
├── README.md               # 说明文档
├── config/                 # 配置模块
│   ├── __init__.py
│   └── models.py           # 模型配置管理
├── routers/                # API路由
│   ├── __init__.py
│   └── models.py           # 模型管理路由
├── tools/                  # 工具模块（37+个工具）
│   ├── __init__.py
│   ├── base.py             # 工具基类
│   ├── file_tools.py       # 文件操作工具
│   ├── search_tools.py     # 搜索工具
│   ├── bash_tool.py        # Bash执行工具
│   ├── agent_tool.py       # Agent管理工具
│   ├── task_tools.py       # 任务管理工具
│   ├── team_tools.py       # 团队管理工具
│   ├── mcp_tool.py         # MCP工具
│   ├── brief_tool.py       # 消息发送工具
│   ├── worktree_tool.py    # Git工作树工具
│   ├── tool_search_tool.py # 工具搜索
│   ├── lsp_tool.py         # LSP代码智能工具
│   └── ...                 # 更多工具
├── agent/                  # Agent模块
│   ├── __init__.py
│   ├── enums.py            # 枚举定义
│   ├── agent.py            # Agent类
│   ├── task.py             # 任务类
│   ├── task_queue.py       # 任务队列
│   ├── manager.py          # Agent管理器
│   └── handlers.py         # 任务处理器
├── services/               # 服务模块
│   ├── __init__.py
│   ├── llm_service.py      # LLM服务
│   └── config_service.py   # 配置服务
└── frontend/               # React前端
    ├── src/
    │   ├── components/     # UI组件
    │   ├── hooks/          # 自定义Hooks
    │   ├── stores/         # Zustand状态管理
    │   ├── services/       # API服务
    │   └── lib/            # 工具函数
    └── package.json
```

## 环境变量配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | OpenAI API密钥 | - |
| `OPENAI_BASE_URL` | OpenAI API基础URL | https://api.openai.com/v1 |
| `ANTHROPIC_API_KEY` | Anthropic API密钥 | - |
| `ANTHROPIC_BASE_URL` | Anthropic API基础URL | https://api.anthropic.com |
| `DEFAULT_MODEL` | 默认模型 | gpt-4o |
| `DEFAULT_MAX_TOKENS` | 默认最大token数 | 4096 |
| `DEFAULT_TEMPERATURE` | 默认温度 | 1.0 |
| `HOST` | 服务主机 | 0.0.0.0 |
| `PORT` | 服务端口 | 8000 |

## 架构设计

### 工具系统

- 基于抽象基类`Tool`，统一接口
- 支持输入验证、错误处理、元数据
- 注册表模式管理工具
- 37+个内置工具

### Agent系统

- `AgentManager`管理Agent生命周期
- 异步任务队列，优先级调度
- 支持团队协作
- 任务处理器可扩展

### LLM服务

- 统一接口支持多提供商
- 支持流式和非流式响应
- 自动错误处理和重试
- 内置12个模型配置

### 对话引擎

- LLM → Tool → Observation → LLM 闭环
- 支持多轮对话
- 工具调用并行执行
- 状态管理和持久化

### API设计

- RESTful风格
- 统一的响应格式
- 自动生成的OpenAPI文档
- CORS支持

## 扩展开发

### 添加新工具

1. 继承`Tool`基类
2. 实现`execute`方法
3. 使用`@register_tool`装饰器注册

```python
from tools.base import Tool, ToolResult, register_tool

@register_tool
class MyTool(Tool[InputType, OutputType]):
    name = "my_tool"
    description = "我的工具"

    async def execute(self, input_data):
        # 实现逻辑
        return ToolResult.ok(data)
```

### 添加新模型

1. 在`config/models.py`中添加模型配置
2. 重启服务即可使用

```python
ModelConfig(
    model_id="my-model",
    name="My Model",
    provider=ModelProvider.OPENAI,
    max_tokens=4096,
    temperature=1.0,
    supports_streaming=True,
    supports_tools=True,
    description="My custom model"
)
```

## License

仅供学习研究使用，源码版权归Anthropic所有。
