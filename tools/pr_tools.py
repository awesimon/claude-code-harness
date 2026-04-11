"""
PR 审查工具
提供 GitHub PR 相关功能
"""

import subprocess
from typing import Optional
from pydantic import BaseModel, Field

from .base import Tool, ToolResult, register_tool


class PRListInput(BaseModel):
    """PR 列表输入"""
    limit: int = Field(10, description="最大数量")


@register_tool
class PRListTool(Tool):
    """列出开放的 PR"""

    name = "pr_list"
    description = """列出所有开放的 Pull Request
使用场景：用户说"列出 PR"、"查看开放的 PR"、"有什么 PR"等"""
    input_model = PRListInput

    async def execute(self, input_data: PRListInput) -> ToolResult:
        """列出 PR"""
        try:
            result = subprocess.run(
                ["gh", "pr", "list", "--limit", str(input_data.limit)],
                capture_output=True,
                text=True,
                check=True
            )

            return ToolResult(
                success=True,
                data={"prs": result.stdout.strip()},
                message="开放的 PR 列表"
            )

        except subprocess.CalledProcessError as e:
            return ToolResult(
                success=False,
                message="获取 PR 列表失败",
                error=str(e)
            )


class PRViewInput(BaseModel):
    """PR 查看输入"""
    number: str = Field(..., description="PR 编号")


@register_tool
class PRViewTool(Tool):
    """查看 PR 详情"""

    name = "pr_view"
    description = """查看指定 PR 的详细信息，包括：
- PR 标题和描述
- 作者和状态
- 更改统计
使用场景：用户说"查看 PR #123"、"PR 详情"等"""
    input_model = PRViewInput

    async def execute(self, input_data: PRViewInput) -> ToolResult:
        """查看 PR"""
        try:
            result = subprocess.run(
                ["gh", "pr", "view", input_data.number],
                capture_output=True,
                text=True,
                check=True
            )

            return ToolResult(
                success=True,
                data={"pr_info": result.stdout.strip()},
                message=f"PR #{input_data.number} 详情"
            )

        except subprocess.CalledProcessError as e:
            return ToolResult(
                success=False,
                message="查看 PR 失败",
                error=str(e)
            )


class PRDiffInput(BaseModel):
    """PR diff 输入"""
    number: str = Field(..., description="PR 编号")


@register_tool
class PRDiffTool(Tool):
    """查看 PR 差异"""

    name = "pr_diff"
    description = """查看 PR 的代码差异
使用场景：用户说"查看 PR 改动"、"PR diff"、"代码审查"等"""
    input_model = PRDiffInput

    async def execute(self, input_data: PRDiffInput) -> ToolResult:
        """获取 PR diff"""
        try:
            result = subprocess.run(
                ["gh", "pr", "diff", input_data.number],
                capture_output=True,
                text=True,
                check=True
            )

            diff_content = result.stdout.strip()

            return ToolResult(
                success=True,
                data={
                    "diff": diff_content[:5000],
                    "is_truncated": len(diff_content) > 5000
                },
                message=f"PR #{input_data.number} 的差异"
            )

        except subprocess.CalledProcessError as e:
            return ToolResult(
                success=False,
                message="获取 PR diff 失败",
                error=str(e)
            )
