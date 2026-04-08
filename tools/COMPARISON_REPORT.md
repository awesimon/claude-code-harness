# Python API 与 TypeScript src 工具对比报告

## 日期: 2026/04/09

---

## 1. src (TypeScript) 完整工具清单

src目录共包含 **53 个工具目录/文件**:

### 核心工具 (P0 - 必须实现)
| # | 工具名称 | 在 python_api 中 |
|---|---------|----------------|
| 1 | AgentTool | 有 (agent_tool.py) |
| 2 | AskUserQuestionTool | 有 (ask_user_tool.py) |
| 3 | BashTool | 有 (bash_tool.py) |
| 4 | BriefTool | **缺失** - 发送消息给用户 |
| 5 | ConfigTool | 有 (config_tool.py) |
| 6 | FileEditTool | 有 (file_tools.py) |
| 7 | FileReadTool | 有 (file_tools.py) |
| 8 | FileWriteTool | 有 (file_tools.py) |
| 9 | GlobTool | 有 (search_tools.py) |
| 10 | GrepTool | 有 (search_tools.py) |
| 11 | NotebookEditTool | 有 (notebook_tool.py) |
| 12 | SkillTool | 有 (skill_tool.py) |
| 13 | TodoWriteTool | 有 (todo_tool.py) |
| 14 | WebFetchTool | 有 (web_fetch_tool.py) |
| 15 | WebSearchTool | 有 (web_search_tool.py) |

### 任务管理工具 (P1 - 高优先级)
| # | 工具名称 | 在 python_api 中 |
|---|---------|----------------|
| 16 | TaskCreateTool | 有 (task_tools.py) |
| 17 | TaskGetTool | 有 (task_tools.py) |
| 18 | TaskListTool | 有 (task_tools.py) |
| 19 | TaskOutputTool | 有 (task_tools.py) |
| 20 | TaskStopTool | 有 (task_tools.py) |
| 21 | TaskUpdateTool | 有 (task_tools.py) |

### Agent协作工具 (P1)
| # | 工具名称 | 在 python_api 中 |
|---|---------|----------------|
| 22 | SendMessageTool | 有 (send_message_tool.py) |
| 23 | TeamCreateTool | 有 (team_tools.py) |
| 24 | TeamDeleteTool | 有 (team_tools.py) |
| 25 | EnterPlanModeTool | 有 (plan_mode_tools.py) |
| 26 | ExitPlanModeTool | 有 (plan_mode_tools.py) |

### MCP 相关工具 (P1)
| # | 工具名称 | 在 python_api 中 |
|---|---------|----------------|
| 27 | MCPTool (基础) | 有 (mcp_tool.py) |
| 28 | ListMcpResourcesTool | **缺失** - 列出MCP资源 |
| 29 | ReadMcpResourceTool | **缺失** - 读取MCP资源 |
| 30 | McpAuthTool | **缺失** - MCP认证 |

### 高级功能工具 (P2 - 中优先级)
| # | 工具名称 | 在 python_api 中 |
|---|---------|----------------|
| 31 | LSPTool | **缺失** - 语言服务器协议支持 |
| 32 | ToolSearchTool | **缺失** - 工具搜索/选择 |
| 33 | EnterWorktreeTool | **缺失** - 创建工作树 |
| 34 | ExitWorktreeTool | **缺失** - 退出工作树 |
| 35 | PowerShellTool | **缺失** - Windows PowerShell执行 |
| 36 | SleepTool | **缺失** - 延迟执行 |
| 37 | SnipTool | **缺失** - 代码片段管理 |

### 计划与验证工具 (P2)
| # | 工具名称 | 在 python_api 中 |
|---|---------|----------------|
| 38 | VerifyPlanExecutionTool | **缺失** - 验证计划执行 |

### 特殊功能工具 (P3 - 低优先级/条件启用)
| # | 工具名称 | 在 python_api 中 | 说明 |
|---|---------|----------------|------|
| 39 | RemoteTriggerTool | **缺失** | 远程触发器，需要特定feature flag |
| 40 | SendUserFileTool | **缺失** | 发送文件给用户，需要KAIROS |
| 41 | TerminalCaptureTool | **缺失** | 终端捕获，需要TERMINAL_PANEL |
| 42 | WebBrowserTool | **缺失** | 浏览器工具，需要WEB_BROWSER_TOOL |
| 43 | WorkflowTool | **缺失** | 工作流脚本，需要WORKFLOW_SCRIPTS |
| 44 | REPLTool | **缺失** | REPL模式，仅ant用户使用 |
| 45 | ReviewArtifactTool | **缺失** | 审查工件，仅占位 |
| 46 | TungstenTool | **缺失** | Ant内部工具，始终禁用 |
| 47 | MonitorTool | **缺失** | 监控工具，实际为null |
| 48 | DiscoverSkillsTool | **缺失** | 仅占位 |
| 49 | OverflowTestTool | N/A | 测试工具，不需要 |
| 50 | SyntheticOutputTool | N/A | 合成输出，内部工具 |
| 51 | TestingPermissionTool | N/A | 测试权限，仅测试用 |

### 共享代码
- shared/ - 工具共享代码
- utils.ts - 工具工具函数

---

## 2. python_api 现有工具

python_api 当前有 **17 个工具文件**, 实现了约 **25+ 个具体工具**:

1. `agent_tool.py` - AgentTool, AgentListTool, AgentDestroyTool
2. `ask_user_tool.py` - AskUserQuestionTool
3. `bash_tool.py` - BashTool, BashBatchTool
4. `config_tool.py` - ConfigGetTool, ConfigSetTool, ConfigDeleteTool, ConfigListTool
5. `file_tools.py` - ReadFileTool, WriteFileTool, EditFileTool
6. `mcp_tool.py` - MCPListServersTool, MCPListToolsTool, MCPExecuteToolTool
7. `notebook_tool.py` - NotebookEditTool
8. `plan_mode_tools.py` - EnterPlanModeTool, ExitPlanModeTool
9. `schedule_cron_tool.py` - ScheduleCreateTool, ScheduleDeleteTool, ScheduleListTool, ScheduleToggleTool
10. `search_tools.py` - GlobTool, GrepTool
11. `send_message_tool.py` - SendMessageTool, MessageHistoryTool
12. `skill_tool.py` - SkillExecuteTool, SkillListTool
13. `task_tools.py` - TaskGetTool, TaskStopTool, TaskOutputTool, TaskCreateTool, TaskUpdateTool, TaskListTool
14. `team_tools.py` - TeamCreateTool, TeamDeleteTool
15. `todo_tool.py` - TodoWriteTool
16. `web_fetch_tool.py` - WebFetchTool
17. `web_search_tool.py` - WebSearchTool

---

## 3. 缺失工具清单 (按优先级)

### P0 - 核心缺失 (立即实现)
1. **BriefTool** - 发送消息给用户，主要的用户输出通道
2. **EnterWorktreeTool** / **ExitWorktreeTool** - 工作树管理
3. **ToolSearchTool** - 工具搜索

### P1 - 高优先级 (近期实现)
4. **LSPTool** - 代码智能（定义跳转、引用查找等）
5. **ListMcpResourcesTool** / **ReadMcpResourceTool** - MCP资源访问
6. **McpAuthTool** - MCP认证
7. **PowerShellTool** - Windows PowerShell支持

### P2 - 中优先级 (后续实现)
8. **SleepTool** - 延迟执行
9. **SnipTool** - 代码片段管理
10. **VerifyPlanExecutionTool** - 计划执行验证

### P3 - 低优先级 (按需实现)
11. RemoteTriggerTool - 远程触发器
12. SendUserFileTool - 发送文件给用户
13. TerminalCaptureTool - 终端捕获
14. WebBrowserTool - 浏览器工具
15. WorkflowTool - 工作流
16. REPLTool - REPL交互

### P4 - 占位/条件工具 (按需实现)
17. ReviewArtifactTool, TungstenTool, MonitorTool, DiscoverSkillsTool

---

## 4. 实现计划

本次将实现以下工具：

1. **brief_tool.py** - BriefTool
2. **worktree_tool.py** - EnterWorktreeTool, ExitWorktreeTool
3. **tool_search_tool.py** - ToolSearchTool
4. **lsp_tool.py** - LSPTool (基础框架)
5. **mcp_resource_tool.py** - ListMcpResourcesTool, ReadMcpResourceTool
6. **mcp_auth_tool.py** - McpAuthTool
7. **powershell_tool.py** - PowerShellTool
8. **sleep_tool.py** - SleepTool
9. **verify_plan_tool.py** - VerifyPlanExecutionTool

并更新 `__init__.py` 导出所有新工具。
