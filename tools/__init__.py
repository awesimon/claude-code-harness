"""
Python API工具模块
为FastAPI提供文件操作、命令执行等核心工具功能
"""

from typing import Any

__version__ = "0.2.0"

__all__ = [
    # 基类
    "Tool",
    "ToolResult",
    "ToolError",
    "ToolRegistry",
    # 文件工具
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    # 搜索工具
    "GlobTool",
    "GrepTool",
    # Bash工具
    "BashTool",
    "BashInput",
    # Agent工具
    "AgentTool",
    "AgentListTool",
    "AgentDestroyTool",
    "AgentToolInput",
    "AgentListInput",
    "AgentDestroyInput",
    # 任务工具
    "TaskGetTool",
    "TaskCreateTool",
    "TaskUpdateTool",
    "TaskListTool",
    "TaskGetInput",
    "TaskCreateInput",
    "TaskUpdateInput",
    "TaskListInput",
    # Web工具
    "WebSearchTool",
    "WebFetchTool",
    "WebSearchInput",
    "WebFetchInput",
    # 团队工具
    "TeamCreateTool",
    "TeamDeleteTool",
    "TeamCreateInput",
    "TeamDeleteInput",
    # 待办工具
    "TodoWriteTool",
    "TodoWriteInput",
    # Notebook工具
    "NotebookEditTool",
    "NotebookEditInput",
    # 计划模式工具
    "EnterPlanModeTool",
    "ExitPlanModeTool",
    # 用户交互工具
    "AskUserQuestionTool",
    "AskUserQuestionInput",
    # 技能工具
    "SkillExecuteTool",
    "SkillListTool",
    "SkillExecuteInput",
    "SkillListInput",
    # Agent通信工具
    "SendMessageTool",
    "MessageHistoryTool",
    "SendMessageInput",
    "MessageHistoryInput",
    # MCP工具
    "MCPListServersTool",
    "MCPListToolsTool",
    "MCPExecuteToolTool",
    "MCPListServersInput",
    "MCPListToolsInput",
    "MCPExecuteToolInput",
    "MCPManager",
    "MCPServer",
    "MCPTool",
    # MCP资源工具
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
    "ListMcpResourcesInput",
    "ReadMcpResourceInput",
    # MCP认证工具
    "McpAuthTool",
    "McpAuthInput",
    # 配置工具
    "ConfigGetTool",
    "ConfigSetTool",
    "ConfigDeleteTool",
    "ConfigListTool",
    "ConfigGetInput",
    "ConfigSetInput",
    "ConfigDeleteInput",
    "ConfigListInput",
    # 定时任务工具
    "ScheduleCreateTool",
    "ScheduleDeleteTool",
    "ScheduleListTool",
    "ScheduleToggleTool",
    "ScheduleCreateInput",
    "ScheduleDeleteInput",
    "ScheduleListInput",
    "ScheduleToggleInput",
    # Brief工具
    "BriefTool",
    "BriefInput",
    # 工作树工具
    "EnterWorktreeTool",
    "ExitWorktreeTool",
    "EnterWorktreeInput",
    "ExitWorktreeInput",
    # 工具搜索
    "ToolSearchTool",
    "ToolSearchInput",
    # PowerShell工具
    "PowerShellTool",
    "PowerShellInput",
    # 睡眠工具
    "SleepTool",
    "SleepInput",
    # LSP工具
    "LSPTool",
    "LSPInput",
    # 验证计划工具
    "VerifyPlanExecutionTool",
    "VerifyPlanInput",
    # Git工具
    "GitStatusTool",
    "GitDiffTool",
    "GitCommitTool",
    "GitStatusInput",
    "GitDiffInput",
    "GitCommitInput",
    # 分支工具
    "BranchListTool",
    "BranchCreateTool",
    "BranchSwitchTool",
    "BranchDeleteTool",
    "BranchListInput",
    "BranchCreateInput",
    "BranchSwitchInput",
    "BranchDeleteInput",
    # PR工具
    "PRListTool",
    "PRViewTool",
    "PRDiffTool",
    "PRListInput",
    "PRViewInput",
    "PRDiffInput",
    # 会话工具
    "SessionSaveTool",
    "SessionListTool",
    "SessionLoadTool",
    "SessionSaveInput",
    "SessionListInput",
    "SessionLoadInput",
    # 系统工具
    "StatsTool",
    "DoctorTool",
    "HelpTool",
    "VersionTool",
    "StatsInput",
    "DoctorInput",
    "HelpInput",
    "VersionInput",
    # Skill 管理工具
    "SkillInstallTool",
    "SkillUninstallTool",
    "SkillListTool",
    "SkillEnableTool",
    "SkillDisableTool",
    "SkillInstallInput",
    "SkillUninstallInput",
    "SkillListInput",
    "SkillEnableInput",
    "SkillDisableInput",
]


# 重新导出基类
from .base import Tool, ToolResult, ToolError, ToolRegistry

# 文件工具
from .file_tools import (
    ReadFileTool, WriteFileTool, EditFileTool,
)

# 搜索工具
from .search_tools import GlobTool, GrepTool

# Bash工具
from .bash_tool import BashTool, BashInput

# Agent工具
from .agent_tool import (
    AgentTool, AgentListTool, AgentDestroyTool,
    AgentToolInput, AgentListInput, AgentDestroyInput,
)

# 任务工具
from .task_tools import (
    TaskGetTool, TaskCreateTool, TaskUpdateTool, TaskListTool,
    TaskGetInput, TaskCreateInput, TaskUpdateInput, TaskListInput,
)

# Web工具
from .web_search_tool import WebSearchTool, WebSearchInput
from .web_fetch_tool import WebFetchTool, WebFetchInput

# 团队工具
from .team_tools import TeamCreateTool, TeamDeleteTool, TeamCreateInput, TeamDeleteInput

# 待办工具
from .todo_tool import TodoWriteTool, TodoWriteInput

# Notebook工具
from .notebook_tool import NotebookEditTool, NotebookEditInput

# 计划模式工具
from .plan_mode_tools import EnterPlanModeTool, ExitPlanModeTool

# 用户交互工具
from .ask_user_tool import AskUserQuestionTool, AskUserQuestionInput

# 技能工具
from .skill_tool import SkillExecuteTool, SkillListTool, SkillExecuteInput, SkillListInput

# Agent通信工具
from .send_message_tool import SendMessageTool, MessageHistoryTool, SendMessageInput, MessageHistoryInput

# MCP工具
from .mcp_tool import (
    MCPListServersTool, MCPListToolsTool, MCPExecuteToolTool,
    MCPListServersInput, MCPListToolsInput, MCPExecuteToolInput,
    MCPManager, MCPServer, MCPTool,
)

# 配置工具
from .config_tool import (
    ConfigGetTool, ConfigSetTool, ConfigDeleteTool, ConfigListTool,
    ConfigGetInput, ConfigSetInput, ConfigDeleteInput, ConfigListInput,
)

# 定时任务工具
from .schedule_cron_tool import (
    ScheduleCreateTool, ScheduleDeleteTool, ScheduleListTool, ScheduleToggleTool,
    ScheduleCreateInput, ScheduleDeleteInput, ScheduleListInput, ScheduleToggleInput,
)

# 新增工具
# Brief工具
from .brief_tool import BriefTool, BriefInput

# 工作树工具
from .worktree_tool import EnterWorktreeTool, ExitWorktreeTool, EnterWorktreeInput, ExitWorktreeInput

# 工具搜索
from .tool_search_tool import ToolSearchTool, ToolSearchInput

# PowerShell工具
from .powershell_tool import PowerShellTool, PowerShellInput

# 睡眠工具
from .sleep_tool import SleepTool, SleepInput

# LSP工具
from .lsp_tool import LSPTool, LSPInput

# MCP资源工具
from .mcp_resource_tool import ListMcpResourcesTool, ReadMcpResourceTool, ListMcpResourcesInput, ReadMcpResourceInput

# MCP认证工具
from .mcp_auth_tool import McpAuthTool, McpAuthInput

# 验证计划工具
from .verify_plan_tool import VerifyPlanExecutionTool, VerifyPlanInput

# Git工具
from .git_tools import (
    GitStatusTool, GitDiffTool, GitCommitTool,
    GitStatusInput, GitDiffInput, GitCommitInput,
)

# 分支工具
from .branch_tools import (
    BranchListTool, BranchCreateTool, BranchSwitchTool, BranchDeleteTool,
    BranchListInput, BranchCreateInput, BranchSwitchInput, BranchDeleteInput,
)

# PR工具
from .pr_tools import (
    PRListTool, PRViewTool, PRDiffTool,
    PRListInput, PRViewInput, PRDiffInput,
)

# 会话工具
from .session_tools import (
    SessionSaveTool, SessionListTool, SessionLoadTool,
    SessionSaveInput, SessionListInput, SessionLoadInput,
)

# 系统工具
from .system_tools import (
    StatsTool, DoctorTool, HelpTool, VersionTool,
    StatsInput, DoctorInput, HelpInput, VersionInput,
)

# Skill 管理工具
from .skill_manager_tools import (
    SkillInstallTool, SkillUninstallTool, SkillListTool, SkillEnableTool, SkillDisableTool,
    SkillInstallInput, SkillUninstallInput, SkillListInput, SkillEnableInput, SkillDisableInput,
)
