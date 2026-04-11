"""
会话管理工具
提供会话保存、加载、恢复功能
"""

import json
import os
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from .base import Tool, ToolResult, register_tool


# 会话存储目录
SESSIONS_DIR = os.path.expanduser("~/.claude_code/sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)


class SessionSaveInput(BaseModel):
    """保存会话输入"""
    name: Optional[str] = Field(None, description="会话名称")


@register_tool
class SessionSaveTool(Tool):
    """保存当前会话"""

    name = "session_save"
    description = """保存当前对话会话，以便稍后恢复
使用场景：用户说"保存会话"、"记住这个对话"、"保存进度"等"""
    input_model = SessionSaveInput

    async def execute(self, input_data: SessionSaveInput) -> ToolResult:
        """保存会话"""
        try:
            session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = input_data.name or f"会话 {datetime.now().strftime('%Y-%m-%d %H:%M')}"

            session_data = {
                "id": session_id,
                "name": name,
                "saved_at": datetime.now().isoformat(),
                "messages": []  # 实际应从 query_engine 获取
            }

            filepath = os.path.join(SESSIONS_DIR, f"{session_id}.json")
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2)

            return ToolResult(
                success=True,
                data={"session_id": session_id, "name": name},
                message=f"会话已保存: {name}"
            )

        except Exception as e:
            return ToolResult(
                success=False,
                message="保存会话失败",
                error=str(e)
            )


class SessionListInput(BaseModel):
    """列出会话输入"""
    limit: int = Field(10, description="最大数量")


@register_tool
class SessionListTool(Tool):
    """列出所有保存的会话"""

    name = "session_list"
    description = """列出所有保存的会话
使用场景：用户说"列出会话"、"查看保存的会话"、"有什么会话"等"""
    input_model = SessionListInput

    async def execute(self, input_data: SessionListInput) -> ToolResult:
        """列出会话"""
        try:
            sessions = []

            files = sorted(
                [f for f in os.listdir(SESSIONS_DIR) if f.endswith('.json')],
                key=lambda x: os.path.getmtime(os.path.join(SESSIONS_DIR, x)),
                reverse=True
            )

            for filename in files[:input_data.limit]:
                filepath = os.path.join(SESSIONS_DIR, filename)
                try:
                    with open(filepath, 'r') as f:
                        data = json.load(f)
                        sessions.append({
                            "id": data.get("id", filename[:-5]),
                            "name": data.get("name", "未命名"),
                            "saved_at": data.get("saved_at")
                        })
                except:
                    continue

            return ToolResult(
                success=True,
                data={"sessions": sessions},
                message=f"找到 {len(sessions)} 个会话"
            )

        except Exception as e:
            return ToolResult(
                success=False,
                message="列出会话失败",
                error=str(e)
            )


class SessionLoadInput(BaseModel):
    """加载会话输入"""
    session_id: str = Field(..., description="会话 ID")


@register_tool
class SessionLoadTool(Tool):
    """加载指定会话"""

    name = "session_load"
    description = """加载之前保存的会话
使用场景：用户说"加载会话"、"恢复会话"、"打开之前的会话"等"""
    input_model = SessionLoadInput

    async def execute(self, input_data: SessionLoadInput) -> ToolResult:
        """加载会话"""
        try:
            filepath = os.path.join(SESSIONS_DIR, f"{input_data.session_id}.json")

            if not os.path.exists(filepath):
                return ToolResult(
                    success=False,
                    message=f"会话 '{input_data.session_id}' 不存在",
                    error="Session not found"
                )

            with open(filepath, 'r') as f:
                data = json.load(f)

            return ToolResult(
                success=True,
                data={
                    "session_id": input_data.session_id,
                    "name": data.get("name"),
                    "messages": data.get("messages", [])
                },
                message=f"已加载会话: {data.get('name')}"
            )

        except Exception as e:
            return ToolResult(
                success=False,
                message="加载会话失败",
                error=str(e)
            )
