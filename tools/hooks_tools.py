"""
Hooks 配置工具
允许用户配置和管理 Claude Code 的 hooks
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from pathlib import Path
import json
import os

from tools.base import Tool, ToolResult, register_tool


# Hooks 配置目录
HOOKS_CONFIG_DIR = Path(os.path.expanduser("~/.claude_code/hooks"))
HOOKS_CONFIG_FILE = HOOKS_CONFIG_DIR / "config.json"

# 支持的 hook 事件类型
HOOK_EVENTS = [
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionDenied",
    "Notification",
    "UserPromptSubmit",
    "SessionStart",
    "Stop",
    "StopFailure",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "PostCompact",
    "SessionEnd",
    "PermissionRequest",
    "Setup",
    "TeammateIdle",
    "TaskCreated",
    "TaskCompleted",
    "Elicitation",
    "ElicitationResult",
    "ConfigChange",
    "InstructionsLoaded",
    "WorktreeCreate",
    "WorktreeRemove",
    "CwdChanged",
    "FileChanged",
]


class HooksConfig:
    """Hooks 配置管理器"""

    def __init__(self):
        self.config_dir = HOOKS_CONFIG_DIR
        self.config_file = HOOKS_CONFIG_FILE
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """加载配置"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {"hooks": []}

    def _save_config(self):
        """保存配置"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Failed to save hooks config: {e}")

    def add_hook(self, event: str, command: str, matcher: Optional[str] = None) -> bool:
        """添加 hook"""
        if event not in HOOK_EVENTS:
            return False

        hook = {
            "event": event,
            "command": command,
        }
        if matcher:
            hook["matcher"] = matcher

        self._config["hooks"].append(hook)
        self._save_config()
        return True

    def remove_hook(self, index: int) -> bool:
        """移除 hook"""
        if 0 <= index < len(self._config["hooks"]):
            self._config["hooks"].pop(index)
            self._save_config()
            return True
        return False

    def list_hooks(self) -> List[Dict[str, Any]]:
        """列出所有 hooks"""
        return self._config["hooks"]

    def get_config(self) -> Dict[str, Any]:
        """获取完整配置"""
        return self._config


# 全局配置实例
_hooks_config = HooksConfig()


class HooksListInput(BaseModel):
    """列出 hooks 输入"""
    pass


@register_tool
class HooksListTool(Tool[HooksListInput, ToolResult]):
    """列出所有已配置的 hooks"""

    name = "hooks_list"
    description = "List all configured hooks"
    input_model = HooksListInput

    async def execute(self, input_data: HooksListInput) -> ToolResult:
        """执行列出 hooks"""
        hooks = _hooks_config.list_hooks()

        if not hooks:
            return ToolResult(
                success=True,
                data={"hooks": [], "count": 0},
                message="No hooks configured"
            )

        # 添加索引
        hooks_with_index = [
            {"index": i, **hook} for i, hook in enumerate(hooks)
        ]

        return ToolResult(
            success=True,
            data={"hooks": hooks_with_index, "count": len(hooks)},
            message=f"{len(hooks)} hook(s) configured"
        )


class HooksAddInput(BaseModel):
    """添加 hook 输入"""
    event: str = Field(..., description=f"Hook event type. One of: {', '.join(HOOK_EVENTS)}")
    command: str = Field(..., description="Command to execute when hook fires")
    matcher: Optional[str] = Field(None, description="Optional matcher (e.g., tool name, file pattern)")


@register_tool
class HooksAddTool(Tool[HooksAddInput, ToolResult]):
    """添加一个 hook"""

    name = "hooks_add"
    description = "Add a new hook configuration"
    input_model = HooksAddInput

    async def execute(self, input_data: HooksAddInput) -> ToolResult:
        """执行添加 hook"""
        success = _hooks_config.add_hook(
            event=input_data.event,
            command=input_data.command,
            matcher=input_data.matcher
        )

        if not success:
            return ToolResult(
                success=False,
                data=None,
                message=f"Invalid hook event: {input_data.event}",
                error=f"Event must be one of: {', '.join(HOOK_EVENTS)}"
            )

        matcher_str = f" (matcher: {input_data.matcher})" if input_data.matcher else ""
        return ToolResult(
            success=True,
            data={"event": input_data.event, "command": input_data.command, "matcher": input_data.matcher},
            message=f"Hook added for '{input_data.event}'{matcher_str}"
        )


class HooksRemoveInput(BaseModel):
    """移除 hook 输入"""
    index: int = Field(..., description="Hook index (from hooks_list)")


@register_tool
class HooksRemoveTool(Tool[HooksRemoveInput, ToolResult]):
    """移除一个 hook"""

    name = "hooks_remove"
    description = "Remove a hook by index"
    input_model = HooksRemoveInput

    async def execute(self, input_data: HooksRemoveInput) -> ToolResult:
        """执行移除 hook"""
        success = _hooks_config.remove_hook(input_data.index)

        if not success:
            return ToolResult(
                success=False,
                data=None,
                message=f"Hook with index {input_data.index} not found",
                error="Invalid hook index"
            )

        return ToolResult(
            success=True,
            data={"index": input_data.index},
            message=f"Hook at index {input_data.index} removed"
        )


class HooksEventsInput(BaseModel):
    """列出 hook 事件输入"""
    pass


@register_tool
class HooksEventsTool(Tool[HooksEventsInput, ToolResult]):
    """列出所有可用的 hook 事件类型"""

    name = "hooks_events"
    description = "List all available hook event types"
    input_model = HooksEventsInput

    async def execute(self, input_data: HooksEventsInput) -> ToolResult:
        """执行列出事件"""
        events_info = {
            "PreToolUse": "Before tool execution - can modify or block tool calls",
            "PostToolUse": "After tool execution - can observe tool results",
            "PostToolUseFailure": "After tool execution fails",
            "PermissionDenied": "After auto mode classifier denies a tool call",
            "Notification": "When notifications are sent",
            "UserPromptSubmit": "When the user submits a prompt",
            "SessionStart": "When a new session is started",
            "Stop": "Right before Claude concludes its response",
            "StopFailure": "When the turn ends due to an API error",
            "SubagentStart": "When a subagent (Agent tool call) is started",
            "SubagentStop": "Right before a subagent concludes its response",
            "PreCompact": "Before conversation compaction",
            "PostCompact": "After conversation compaction",
            "SessionEnd": "When a session is ending",
            "PermissionRequest": "When a permission dialog is displayed",
            "Setup": "Repo setup hooks for init and maintenance",
            "TeammateIdle": "When a teammate is about to go idle",
            "TaskCreated": "When a task is being created",
            "TaskCompleted": "When a task is being marked as completed",
            "Elicitation": "When an MCP server requests user input",
            "ElicitationResult": "After a user responds to an MCP elicitation",
            "ConfigChange": "When configuration files change during a session",
            "InstructionsLoaded": "When an instruction file (CLAUDE.md or rule) is loaded",
            "WorktreeCreate": "Create an isolated worktree for VCS-agnostic isolation",
            "WorktreeRemove": "Remove a previously created worktree",
            "CwdChanged": "After the working directory changes",
            "FileChanged": "When a watched file changes",
        }

        return ToolResult(
            success=True,
            data={
                "events": [
                    {"name": event, "description": events_info.get(event, "")}
                    for event in HOOK_EVENTS
                ],
                "count": len(HOOK_EVENTS)
            },
            message=f"{len(HOOK_EVENTS)} hook events available"
        )
