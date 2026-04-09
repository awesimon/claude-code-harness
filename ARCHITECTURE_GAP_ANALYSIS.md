# Claude Code vs Python API 架构差距分析报告

## 一、Claude Code 核心架构总结

### 1.1 QueryEngine 核心设计

Claude Code 的 `QueryEngine.ts` (1295行) 是一个高度复杂的对话引擎：

#### 核心特性
| 特性 | 实现方式 | 复杂度 |
|------|----------|--------|
| **消息流** | AsyncGenerator<SDKMessage> 流式输出 | 高 |
| **配置系统** | QueryEngineConfig 含20+配置项 | 高 |
| **状态管理** | AppState + 持久化存储 | 高 |
| **权限系统** | canUseTool + permission modes | 高 |
| **预算控制** | maxBudgetUsd + maxTurns | 中 |
| **历史压缩** | HISTORY_SNIP 功能 | 高 |
| **Agent系统** | 内置Agent + 自定义Agent | 高 |
| **MCP支持** | 外部MCP服务器集成 | 中 |

#### 消息类型系统 (line 757+)
```typescript
switch (message.type) {
  case 'tombstone': // 墓碑消息（已删除）
  case 'assistant': // 助手消息
  case 'progress':  // 进度消息
  case 'user':      // 用户消息
  case 'stream_event': // 流事件
  case 'attachment':   // 附件消息
  case 'stream_request_start': // 流请求开始
  case 'system':    // 系统消息
  case 'tool_use_summary': // 工具使用摘要
}
```

### 1.2 Plan Mode 计划模式

#### EnterPlanModeTool (EnterPlanModeTool.ts)
- **功能**: 请求进入计划模式
- **触发**: LLM 自主决定调用
- **行为**: 
  - 设置 `toolPermissionContext.mode = 'plan'`
  - 禁止文件写入操作（只读探索）
  - 准备计划上下文

#### ExitPlanModeV2Tool (ExitPlanModeV2Tool.ts)
- **功能**: 退出计划模式并提交计划
- **流程**:
  1. 保存计划到文件 (`{planSlug}.md`)
  2. 用户审批流程
  3. 恢复之前的权限模式
  4. 支持 Agent 团队的计划审批

#### 计划文件管理 (plans.ts)
- 计划存储在 `~/.claude/plans/{word-slug}.md`
- 支持会话恢复时计划恢复
- 支持 Fork 时的计划复制
- 文件快照持久化（远程会话）

### 1.3 Agent 系统

#### 内置 Agent 类型

| Agent | 用途 | 限制 |
|-------|------|------|
| **Explore** | 代码库探索 | 禁止写操作、禁止嵌套Agent |
| **Plan** | 架构设计 | 禁止写操作、禁止嵌套Agent |
| **Code** | 代码实现 | 继承父级工具 |
| **Test** | 测试编写 | 专注测试工具 |

#### Fork Subagent 机制 (forkSubagent.ts)
- **隐式Fork**: 省略 `subagent_type` 时触发
- **上下文继承**: 子Agent继承完整对话历史
- **后台执行**: 所有Agent spawn异步执行
- **Prompt缓存优化**: 通过字节级相同前缀最大化缓存命中

#### Agent 配置结构
```typescript
interface BuiltInAgentDefinition {
  agentType: string
  whenToUse: string          // LLM选择Agent的提示
  tools: string[]            // 可用工具列表
  disallowedTools: string[]  // 禁止的工具
  maxTurns: number          // 最大回合数
  model: 'inherit' | string  // 模型选择
  permissionMode: 'bubble' | 'local'  // 权限模式
  omitClaudeMd: boolean     // 是否省略CLAUDE.md
  getSystemPrompt: () => string  // 系统提示词生成
}
```

### 1.4 权限系统

#### 权限模式
| 模式 | 说明 |
|------|------|
| `default` | 默认模式，每次询问 |
| `auto` | 自动模式，信任LLM决策 |
| `plan` | 计划模式，只读探索 |
| `bypass` | 绕过权限检查 |

#### 权限检查流程
1. `checkPermissions()` - 工具调用前检查
2. `canUseTool()` - 用户定义的权限回调
3. `applyPermissionUpdate()` - 应用权限更新

### 1.5 工具系统

#### 工具定义结构
```typescript
interface ToolDef<Input, Output> {
  name: string
  description: () => string | Promise<string>
  prompt: () => string | Promise<string>
  inputSchema: z.ZodSchema
  outputSchema: z.ZodSchema
  isEnabled: () => boolean
  isReadOnly: () => boolean
  shouldDefer: boolean        // 是否延迟执行
  isConcurrencySafe: () => boolean
  requiresUserInteraction: () => boolean
  validateInput: (input, context) => Promise<ValidationResult>
  checkPermissions: (input, context) => Promise<PermissionResult>
  call: (input, context) => Promise<ToolResult<Output>>
  renderToolUseMessage: (input) => ReactNode
  renderToolResultMessage: (output) => ReactNode
  mapToolResultToToolResultBlockParam: (output, toolUseId) => ToolResultBlockParam
}
```

---

## 二、Python API 当前架构

### 2.1 QueryEngine 实现 (query_engine.py)

当前实现约 **624行**，是一个简化版本：

#### 已实现功能
- [x] 基础对话循环 (LLM → Tool → Observation)
- [x] 流式响应支持 (`chat_stream`)
- [x] 状态管理 (`ConversationState`)
- [x] 工具注册和调用 (`ToolRegistry`)
- [x] 并行工具执行 (`asyncio.gather`)
- [x] 多Provider支持 (OpenAI, Anthropic)

#### 核心类结构
```python
@dataclass
class ConversationContext:
    conversation_id: str
    messages: List[ConversationTurn]
    state: ConversationState
    metadata: Dict[str, Any]

class QueryEngine:
    def __init__(self, llm_service, max_iterations, provider, model)
    def create_conversation(self) -> str
    def chat(self) -> AsyncIterator[Dict]  # 非流式
    def chat_stream(self) -> AsyncIterator[Dict]  # 流式
    def _execute_tools(self) -> List[ToolObservation]
```

### 2.2 工具系统

当前工具注册方式：
```python
# tools/base.py
class ToolRegistry:
    _tools: Dict[str, Tool] = {}

    @classmethod
    def register(cls, tool: Tool):
        cls._tools[tool.name] = tool
```

### 2.3 现有 API 端点

| 端点 | 功能 |
|------|------|
| `/tools/*` | 工具执行 |
| `/agents` | Agent管理 |
| `/tasks` | 任务调度 |
| `/teams` | 团队管理 |
| `/api/models` | 模型管理 |
| `/chat/*` | 对话接口 |
| `/llm/*` | LLM接口 |

---

## 三、架构差距详细对比

### 3.1 核心引擎差距

| 功能 | Claude Code | Python API | 差距等级 |
|------|-------------|------------|----------|
| **消息类型系统** | 9+消息类型，复杂switch处理 | 简单事件字典 | 🔴 高 |
| **历史压缩** | HISTORY_SNIP 智能压缩 | 无 | 🔴 高 |
| **预算控制** | maxBudgetUsd + maxTurns | 仅max_iterations | 🟡 中 |
| **会话持久化** | 完整transcript记录 | 内存存储 | 🔴 高 |
| **状态管理** | AppState + 持久化 | 简单内存状态 | 🟡 中 |
| **流式处理** | 完整SSE流 | 基础流式 | 🟢 低 |
| **权限系统** | 多模式权限系统 | 无 | 🔴 高 |
| **Agent系统** | 内置+自定义+Fork | 基础Agent | 🔴 高 |
| **MCP支持** | 完整MCP集成 | 无 | 🔴 高 |
| **Thinking配置** | 可配置思考模式 | 无 | 🟡 中 |
| **结构化输出** | JSON Schema支持 | 无 | 🟡 中 |

### 3.2 Plan Mode 差距

| 功能 | Claude Code | Python API | 差距 |
|------|-------------|------------|------|
| EnterPlanModeTool | ✅ 完整实现 | ❌ 无 | 🔴 |
| ExitPlanModeTool | ✅ 完整实现 | ❌ 无 | 🔴 |
| 计划文件管理 | ✅ 自动保存/恢复 | ❌ 无 | 🔴 |
| 用户审批流程 | ✅ 交互式审批 | ❌ 无 | 🔴 |
| Agent计划审批 | ✅ 团队领导审批 | ❌ 无 | 🔴 |
| 计划恢复 | ✅ 会话恢复时恢复 | ❌ 无 | 🟡 |

### 3.3 Agent 系统差距

| 功能 | Claude Code | Python API | 差距 |
|------|-------------|------------|------|
| 内置Agent | ✅ Explore/Plan/Code/Test | ⚠️ 基础框架 | 🔴 |
| Agent定义 | ✅ 完整配置结构 | ⚠️ 简单定义 | 🔴 |
| Fork机制 | ✅ 隐式Fork+上下文继承 | ❌ 无 | 🔴 |
| Prompt缓存优化 | ✅ 字节级优化 | ❌ 无 | 🟡 |
| 工具过滤 | ✅ 允许/禁止列表 | ⚠️ 基础 | 🟡 |
| 权限模式 | ✅ bubble/local | ❌ 无 | 🔴 |
| Agent团队 | ✅ 多Agent协作 | ⚠️ 基础 | 🔴 |

### 3.4 工具系统差距

| 功能 | Claude Code | Python API | 差距 |
|------|-------------|------------|------|
| 工具定义 | ✅ 完整ToolDef结构 | ⚠️ 基础schema | 🔴 |
| 延迟执行 | ✅ shouldDefer | ❌ 无 | 🟡 |
| 权限检查 | ✅ checkPermissions | ❌ 无 | 🔴 |
| 输入验证 | ✅ validateInput | ⚠️ 基础 | 🟡 |
| UI渲染 | ✅ React组件 | ❌ 无 | 🟡 |
| 结果映射 | ✅ mapToolResultToBlock | ⚠️ 基础 | 🟡 |
| 并发安全 | ✅ isConcurrencySafe | ❌ 无 | 🟡 |
| 用户交互 | ✅ requiresUserInteraction | ❌ 无 | 🟡 |

---

## 四、改造路线图

### Phase 1: 核心引擎增强 (2-3周)

#### 4.1.1 消息类型系统重构
```python
# 目标: 实现类似的消息类型系统
class MessageType(Enum):
    TOMBSTONE = "tombstone"
    ASSISTANT = "assistant"
    PROGRESS = "progress"
    USER = "user"
    STREAM_EVENT = "stream_event"
    ATTACHMENT = "attachment"
    SYSTEM = "system"
    TOOL_USE_SUMMARY = "tool_use_summary"

@dataclass
class SDKMessage:
    type: MessageType
    content: Any
    uuid: str
    timestamp: str
    # ... 其他字段
```

#### 4.1.2 会话持久化
```python
# 新增 transcript.py
class TranscriptManager:
    async def record(self, messages: List[SDKMessage])
    async def load(self, session_id: str) -> List[SDKMessage]
    async def compact(self)  # 历史压缩
```

#### 4.1.3 状态管理增强
```python
# 新增 state.py
@dataclass
class AppState:
    tool_permission_context: ToolPermissionContext
    session_id: str
    file_state_cache: FileStateCache
    # ... 其他状态
```

### Phase 2: Plan Mode 实现 (1-2周)

#### 4.2.1 EnterPlanModeTool
```python
# tools/EnterPlanModeTool.py
class EnterPlanModeTool(Tool):
    name = "EnterPlanMode"
    
    async def call(self, input, context):
        # 1. 验证当前不在plan模式
        # 2. 设置 mode = 'plan'
        # 3. 准备计划上下文
        # 4. 返回确认消息
```

#### 4.2.2 ExitPlanModeTool
```python
# tools/ExitPlanModeTool.py
class ExitPlanModeTool(Tool):
    name = "ExitPlanMode"
    
    async def call(self, input, context):
        # 1. 保存计划到文件
        # 2. 等待用户审批
        # 3. 恢复之前的权限模式
        # 4. 返回计划内容
```

#### 4.2.3 计划文件管理
```python
# utils/plans.py
def get_plan_file_path(agent_id: Optional[str] = None) -> Path
def get_plan(agent_id: Optional[str] = None) -> Optional[str]
async def save_plan(content: str, agent_id: Optional[str] = None)
```

### Phase 3: Agent 系统重构 (2-3周)

#### 4.3.1 Agent 定义结构
```python
# agent/types.py
@dataclass
class AgentDefinition:
    agent_type: str
    when_to_use: str
    tools: List[str]  # ['*'] 表示全部
    disallowed_tools: List[str]
    max_turns: int
    model: Union[str, Literal["inherit"]]
    permission_mode: Literal["bubble", "local"]
    omit_claude_md: bool
    get_system_prompt: Callable[[], str]
```

#### 4.3.2 内置 Agent 实现
```python
# agent/built_in.py
EXPLORE_AGENT = AgentDefinition(
    agent_type="Explore",
    when_to_use="Fast agent for exploring codebases...",
    tools=["Glob", "Grep", "Read", "Bash"],
    disallowed_tools=["Agent", "Edit", "Write"],
    max_turns=50,
    model="inherit",
    permission_mode="bubble",
    omit_claude_md=True,
    get_system_prompt=get_explore_system_prompt,
)

PLAN_AGENT = AgentDefinition(...)
CODE_AGENT = AgentDefinition(...)
```

#### 4.3.3 Fork Subagent 机制
```python
# agent/fork.py
def build_forked_messages(
    directive: str,
    assistant_message: AssistantMessage
) -> List[Message]:
    # 1. 克隆完整助手消息
    # 2. 为每个tool_use构建placeholder tool_result
    # 3. 添加子Agent指令
    # 4. 返回消息列表
```

### Phase 4: 权限系统 (1-2周)

#### 4.4.1 权限模式
```python
# permissions/types.py
class PermissionMode(Enum):
    DEFAULT = "default"
    AUTO = "auto"
    PLAN = "plan"
    BYPASS = "bypass"

@dataclass
class ToolPermissionContext:
    mode: PermissionMode
    pre_plan_mode: Optional[PermissionMode]
    stripped_dangerous_rules: Optional[List[str]]
```

#### 4.4.2 权限检查
```python
# permissions/checker.py
class PermissionChecker:
    async def check(
        self,
        tool: Tool,
        input: Dict,
        context: ToolContext
    ) -> PermissionResult:
        # 1. 检查当前模式
        # 2. 应用权限规则
        # 3. 返回结果 (allow/ask/deny)
```

### Phase 5: 工具系统增强 (1-2周)

#### 4.5.1 完整 Tool 定义
```python
# tools/base.py
class Tool(ABC):
    name: str
    description: Union[str, Callable[[], str]]
    prompt: Union[str, Callable[[], str]]
    input_schema: Dict  # JSON Schema
    output_schema: Dict
    
    def is_enabled(self) -> bool: ...
    def is_read_only(self) -> bool: ...
    def should_defer(self) -> bool: ...
    def is_concurrency_safe(self) -> bool: ...
    
    async def validate_input(self, input, context) -> ValidationResult: ...
    async def check_permissions(self, input, context) -> PermissionResult: ...
    async def call(self, input, context) -> ToolResult: ...
```

---

## 五、关键设计决策

### 5.1 消息流设计

**Claude Code 使用 AsyncGenerator 实现真正的流式处理：**
```typescript
async function* submitMessage(): AsyncGenerator<SDKMessage> {
  yield { type: 'progress', content: 'thinking' }
  const response = await llm.call()
  yield { type: 'assistant', content: response }
}
```

**Python API 应该采用相同模式：**
```python
async def submit_message(
    self,
    message: str
) -> AsyncIterator[SDKMessage]:
    yield SDKMessage(type='progress', content='thinking')
    async for chunk in self.llm.stream(message):
        yield SDKMessage(type='assistant', content=chunk)
```

### 5.2 Agent 执行模型

**Claude Code 的 Agent 执行是异步的：**
- Agent 在后台执行
- 通过 Mailbox 系统通信
- 父级通过 TaskNotification 接收结果

**Python API 需要实现：**
```python
# 异步Agent执行
async def spawn_agent(
    self,
    agent_def: AgentDefinition,
    directive: str,
    parent_context: ConversationContext
) -> str:  # 返回task_id
    # 1. 创建子上下文
    # 2. 在后台启动执行
    # 3. 返回task_id供查询
```

### 5.3 权限与 Plan Mode 集成

**关键流程：**
1. LLM 调用 `EnterPlanModeTool`
2. 系统设置 `mode = 'plan'`
3. 所有写操作工具被禁用
4. LLM 探索并设计计划
5. LLM 调用 `ExitPlanModeTool`
6. 保存计划并等待用户审批
7. 用户批准后恢复之前的模式
8. LLM 执行计划

---

## 六、实现优先级建议

### P0 (核心功能)
1. ✅ 基础对话循环 - 已实现
2. ✅ 流式响应 - 已实现
3. 🔄 Plan Mode 系统 - 高优先级
4. 🔄 Agent 系统重构 - 高优先级

### P1 (增强功能)
5. 权限系统
6. 会话持久化
7. 历史压缩
8. MCP 支持

### P2 (优化功能)
9. 预算控制
10. Thinking 配置
11. 结构化输出
12. Prompt 缓存优化

---

## 七、文件结构建议

```
python_api/
├── main.py                    # FastAPI 入口
├── query_engine.py            # 核心引擎 (需要大幅重构)
├── config/
│   ├── models.py             # 模型配置
│   └── settings.py           # 应用设置
├── core/                     # 新增: 核心模块
│   ├── __init__.py
│   ├── messages.py           # 消息类型系统
│   ├── state.py              # 状态管理
│   ├── transcript.py         # 会话持久化
│   └── streaming.py          # 流式处理
├── agents/                   # 新增: Agent系统
│   ├── __init__.py
│   ├── types.py              # Agent定义类型
│   ├── engine.py             # Agent执行引擎
│   ├── fork.py               # Fork子Agent
│   └── built_in/             # 内置Agent
│       ├── __init__.py
│       ├── explore.py
│       ├── plan.py
│       └── code.py
├── plan/                     # 新增: Plan Mode
│   ├── __init__.py
│   ├── mode.py               # 模式管理
│   ├── storage.py            # 计划存储
│   └── approval.py           # 审批流程
├── permissions/              # 新增: 权限系统
│   ├── __init__.py
│   ├── types.py              # 权限类型
│   ├── checker.py            # 权限检查
│   └── modes.py              # 模式管理
├── tools/                    # 现有: 工具目录
│   ├── base.py               # 需要增强
│   └── ...
├── routers/                  # 现有: API路由
│   └── ...
└── services/                 # 现有: 服务层
    └── ...
```

---

## 八、总结

Python API 目前是一个功能基础但架构简化的实现。要达到 Claude Code 的能力，需要在以下方面进行深度改造：

1. **核心引擎**: 从简单的事件流升级为完整的消息类型系统
2. **Plan Mode**: 实现完整的计划模式生命周期管理
3. **Agent系统**: 重构为支持Fork、内置Agent类型、权限模式的完整系统
4. **权限系统**: 实现多模式权限管理和审批流程
5. **工具系统**: 增强工具定义结构，支持延迟执行、权限检查等

预计完整改造需要 **6-10周** 的开发时间，建议按Phase分阶段实施，每个Phase可独立交付。
