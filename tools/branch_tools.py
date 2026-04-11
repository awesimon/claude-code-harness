"""
分支管理工具集
提供 Git 分支操作功能
"""

import subprocess
from typing import Optional
from pydantic import BaseModel, Field

from .base import Tool, ToolResult, register_tool


class BranchListInput(BaseModel):
    """分支列表输入"""
    all: bool = Field(False, description="显示所有分支包括远程")


@register_tool
class BranchListTool(Tool):
    """列出所有分支"""

    name = "branch_list"
    description = """列出所有 Git 分支，显示：
- 当前分支
- 本地分支
- 远程分支（可选）
使用场景：用户说"列出分支"、"查看所有分支"、"切换分支前查看"等"""
    input_model = BranchListInput

    async def execute(self, input_data: BranchListInput) -> ToolResult:
        """获取分支列表"""
        try:
            cmd = ["git", "branch"]
            if input_data.all:
                cmd.append("-a")
            cmd.append("-vv")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            branches = []
            current = None

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                line = line.strip()
                is_current = line.startswith("*")
                if is_current:
                    line = line[1:].strip()
                    current = line.split()[0]
                branches.append({
                    "name": line.split()[0],
                    "current": is_current
                })

            return ToolResult(
                success=True,
                data={
                    "branches": branches,
                    "current": current
                },
                message=f"当前分支: {current}"
            )

        except subprocess.CalledProcessError as e:
            return ToolResult(
                success=False,
                message="获取分支列表失败",
                error=str(e)
            )


class BranchCreateInput(BaseModel):
    """创建分支输入"""
    name: str = Field(..., description="新分支名称")
    checkout: bool = Field(True, description="是否切换到新分支")


@register_tool
class BranchCreateTool(Tool):
    """创建新分支"""

    name = "branch_create"
    description = """创建新的 Git 分支，可以：
- 从当前分支创建
- 自动切换到新分支
使用场景：用户说"创建分支"、"新建分支"、"开新分支"等"""
    input_model = BranchCreateInput

    async def execute(self, input_data: BranchCreateInput) -> ToolResult:
        """创建分支"""
        try:
            # 检查分支是否存在
            check = subprocess.run(
                ["git", "branch", "--list", input_data.name],
                capture_output=True,
                text=True
            )

            if check.stdout.strip():
                return ToolResult(
                    success=False,
                    message=f"分支 '{input_data.name}' 已存在",
                    error="Branch already exists"
                )

            # 创建分支
            subprocess.run(
                ["git", "branch", input_data.name],
                capture_output=True,
                text=True,
                check=True
            )

            # 切换分支
            if input_data.checkout:
                subprocess.run(
                    ["git", "checkout", input_data.name],
                    capture_output=True,
                    text=True,
                    check=True
                )

            return ToolResult(
                success=True,
                data={"branch_name": input_data.name},
                message=f"已创建{'并切换到' if input_data.checkout else ''}分支 '{input_data.name}'"
            )

        except subprocess.CalledProcessError as e:
            return ToolResult(
                success=False,
                message="创建分支失败",
                error=str(e)
            )


class BranchSwitchInput(BaseModel):
    """切换分支输入"""
    name: str = Field(..., description="分支名称")


@register_tool
class BranchSwitchTool(Tool):
    """切换到指定分支"""

    name = "branch_switch"
    description = """切换到指定的 Git 分支
使用场景：用户说"切换分支"、"切换到 xxx 分支"、"checkout 分支"等"""
    input_model = BranchSwitchInput

    async def execute(self, input_data: BranchSwitchInput) -> ToolResult:
        """切换分支"""
        try:
            result = subprocess.run(
                ["git", "checkout", input_data.name],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                return ToolResult(
                    success=False,
                    message=f"切换分支失败",
                    error=result.stderr
                )

            return ToolResult(
                success=True,
                data={"branch_name": input_data.name},
                message=f"已切换到分支 '{input_data.name}'"
            )

        except subprocess.CalledProcessError as e:
            return ToolResult(
                success=False,
                message="切换分支失败",
                error=str(e)
            )


class BranchDeleteInput(BaseModel):
    """删除分支输入"""
    name: str = Field(..., description="分支名称")
    force: bool = Field(False, description="强制删除")


@register_tool
class BranchDeleteTool(Tool):
    """删除分支"""

    name = "branch_delete"
    description = """删除 Git 分支
使用场景：用户说"删除分支"、"移除分支"等"""
    input_model = BranchDeleteInput

    async def execute(self, input_data: BranchDeleteInput) -> ToolResult:
        """删除分支"""
        try:
            # 获取当前分支
            current = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True,
                text=True,
                check=True
            )

            if current.stdout.strip() == input_data.name:
                return ToolResult(
                    success=False,
                    message="无法删除当前分支",
                    error="Cannot delete current branch"
                )

            flag = "-D" if input_data.force else "-d"
            subprocess.run(
                ["git", "branch", flag, input_data.name],
                capture_output=True,
                text=True,
                check=True
            )

            return ToolResult(
                success=True,
                data={"branch_name": input_data.name},
                message=f"已删除分支 '{input_data.name}'"
            )

        except subprocess.CalledProcessError as e:
            return ToolResult(
                success=False,
                message="删除分支失败",
                error=str(e)
            )
