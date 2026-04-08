# Claude Code Python API

将Claude Code核心功能改造成Python FastAPI服务端接口，支持OpenAI/Anthropic LLM调用。

## 功能特性

- **工具API**: 文件操作（读/写/编辑）、代码搜索（Glob/Grep）、Bash命令执行
- **Agent管理**: 创建/管理Agent、任务分配、并行执行
- **任务调度**: 优先级队列、异步执行、状态跟踪
- **LLM集成**: 支持OpenAI和Anthropic API
- **流式响应**: 支持SSE流式输出

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
# 使用 uvicorn
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 或使用 Python
python main.py
```

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

### LLM聊天 (OpenAI)

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

### LLM聊天 (Anthropic)

```bash
curl -X POST http://localhost:8000/llm/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "你好，请介绍一下自己"}
    ],
    "model": "claude-3-sonnet-20240229",
    "provider": "anthropic"
  }'
```

### 流式聊天

```bash
curl -X POST http://localhost:8000/llm/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "讲个故事"}
    ],
    "stream": true
  }'
```

### 创建Agent

```bash
curl -X POST http://localhost:8000/agents \
  -H "Content-Type: application/json" \
  -d '{"name": "Worker-1", "capabilities": ["bash", "file"]}'
```

### 创建任务

```bash
curl -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "description": "列出当前目录",
    "task_type": "local_bash",
    "input_data": {"command": "ls -la"},
    "priority": "normal"
  }'
```

## 项目结构

```
python_api/
├── main.py              # FastAPI主应用
├── pyproject.toml       # uv项目配置
├── .env.example         # 环境变量示例
├── README.md           # 说明文档
├── tools/              # 工具模块
│   ├── __init__.py
│   ├── base.py         # 工具基类
│   ├── file_tools.py   # 文件操作工具
│   ├── search_tools.py # 搜索工具
│   └── bash_tool.py    # Bash执行工具
├── agent/              # Agent模块
│   ├── __init__.py
│   ├── enums.py        # 枚举定义
│   ├── agent.py        # Agent类
│   ├── task.py         # 任务类
│   ├── task_queue.py   # 任务队列
│   ├── manager.py      # Agent管理器
│   └── handlers.py     # 任务处理器
└── services/           # 服务模块
    ├── __init__.py
    ├── llm_service.py  # LLM服务(OpenAI/Anthropic)
    └── config_service.py # 配置服务
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
| `DEFAULT_TEMPERATURE` | 默认温度 | 0.7 |
| `HOST` | 服务主机 | 0.0.0.0 |
| `PORT` | 服务端口 | 8000 |

## 架构设计

### 工具系统

- 基于抽象基类`Tool`，统一接口
- 支持输入验证、错误处理、元数据
- 注册表模式管理工具

### Agent系统

- `AgentManager`管理Agent生命周期
- 异步任务队列，优先级调度
- 任务处理器可扩展

### LLM服务

- 统一接口支持多提供商(OpenAI/Anthropic)
- 支持流式和非流式响应
- 自动错误处理和重试

### API设计

- RESTful风格
- 统一的响应格式
- 自动生成的OpenAPI文档

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

### 添加新LLM提供商

1. 在`LLMProvider`枚举中添加
2. 在`LLMService`中实现对应方法

## License

仅供学习研究使用，源码版权归Anthropic所有。
