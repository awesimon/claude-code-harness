# Claude Code Python API 功能对比报告

## 已实现功能 (22个工具)

### 文件操作
- ✅ read_file - 读取文件
- ✅ write_file - 写入文件
- ✅ edit_file - 编辑文件
- ✅ glob - 文件搜索
- ✅ grep - 内容搜索

### 命令执行
- ✅ bash - Bash命令执行
- ✅ bash_batch - 批量Bash命令

### Agent系统
- ✅ agent - 创建Agent
- ✅ agent_list - 列出Agent
- ✅ agent_destroy - 销毁Agent

### 任务管理
- ✅ task_get - 获取任务
- ✅ task_stop - 停止任务
- ✅ task_output - 获取任务输出

### 团队协作
- ✅ team_create - 创建团队
- ✅ team_delete - 删除团队

### 其他工具
- ✅ todo_write - 待办事项
- ✅ notebook_edit - Notebook编辑
- ✅ web_search - 网络搜索
- ✅ web_fetch - 网页获取
- ✅ ask_user_question - 询问用户
- ✅ enter_plan_mode - 进入计划模式
- ✅ exit_plan_mode - 退出计划模式

## 未实现功能 (与src对比)

### 核心功能缺失
| 工具 | 优先级 | 说明 |
|------|--------|------|
| ❌ SkillTool | 高 | 技能系统，加载执行技能 |
| ❌ SendMessageTool | 高 | 子Agent消息发送 |
| ❌ TaskCreateTool/Update/List | 高 | 完整任务生命周期管理 |
| ❌ ConfigTool | 中 | 配置管理 |

### 开发工具
| 工具 | 优先级 | 说明 |
|------|--------|------|
| ❌ LSPTool | 中 | LSP语言服务器支持 |
| ❌ MCPTool | 高 | MCP工具调用 |
| ❌ ListMcpResourcesTool | 中 | MCP资源列表 |
| ❌ ReadMcpResourceTool | 中 | 读取MCP资源 |

### 高级功能
| 工具 | 优先级 | 说明 |
|------|--------|------|
| ❌ EnterWorktreeTool | 低 | Git worktree支持 |
| ❌ ExitWorktreeTool | 低 | 退出worktree |
| ❌ BriefTool | 低 | 摘要生成 |
| ❌ TungstenTool | 低 | 内部工具 |
| ❌ WebBrowserTool | 低 | 浏览器控制 |
| ❌ PowerShellTool | 低 | Windows PowerShell |
| ❌ REPLTool | 低 | REPL交互 |
| ❌ WorkflowTool | 低 | 工作流脚本 |

### 调度与监控
| 工具 | 优先级 | 说明 |
|------|--------|------|
| ❌ ScheduleCronTool | 中 | Cron定时任务 |
| ❌ RemoteTriggerTool | 中 | 远程触发器 |
| ❌ MonitorTool | 低 | 监控工具 |
| ❌ SleepTool | 低 | 休眠等待 |

### 其他
| 工具 | 优先级 | 说明 |
|------|--------|------|
| ❌ SnipTool | 低 | 代码片段 |
| ❌ ToolSearchTool | 低 | 工具搜索 |
| ❌ VerifyPlanExecutionTool | 低 | 计划验证 |
| ❌ TerminalCaptureTool | 低 | 终端捕获 |
| ❌ SendUserFileTool | 低 | 发送用户文件 |

## 架构差异

### Python API 当前架构
```
main.py (FastAPI)
  ├── QueryEngine (核心对话循环)
  ├── ToolRegistry (工具注册)
  ├── AgentManager (Agent管理)
  └── LLMService (LLM调用)
```

### 原始 src 架构
```
commands.ts (命令入口)
  ├── QueryEngine.ts (查询引擎)
  ├── Tool.ts (工具基类)
  ├── coordinator/ (协调器模式)
  ├── skills/ (技能系统)
  ├── tasks/ (任务系统)
  └── services/ (各种服务)
```

## 关键缺失

### 1. 技能系统 (SkillSystem)
- 技能加载机制
- 技能执行环境
- 内置技能支持

### 2. 协调器模式 (Coordinator Mode)
- 多Agent协调
- 任务分配策略
- 结果聚合

### 3. MCP支持
- MCP服务器连接
- MCP工具调用
- MCP资源访问

### 4. 完整任务系统
- 任务创建/更新/列表
- 任务依赖管理
- 任务状态追踪

### 5. 前端界面
- 对话界面优化
- 工具调用可视化
- 计划模式UI

## 建议实现优先级

### P0 (核心功能)
1. SkillTool - 技能系统
2. SendMessageTool - Agent通信
3. TaskCreateTool/TaskUpdateTool/TaskListTool - 完整任务管理

### P1 (重要功能)
4. MCPTool - MCP支持
5. ConfigTool - 配置管理
6. ScheduleCronTool - 定时任务

### P2 (增强功能)
7. LSPTool - LSP支持
8. BriefTool - 摘要生成
9. WebBrowserTool - 浏览器控制

### P3 (可选功能)
10. 其他工具根据需求逐步实现
