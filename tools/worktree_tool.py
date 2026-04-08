"""
WorktreeTool - Git工作树管理
支持创建和退出Git工作树，用于隔离开发环境
"""

import os
import subprocess
from dataclasses import dataclass
from typing import Optional

from .base import Tool, ToolResult, ToolError


@dataclass
class EnterWorktreeInput:
    """进入工作树输入"""
    name: Optional[str] = None  # 工作树名称，不指定则自动生成
    base_branch: Optional[str] = None  # 基于哪个分支创建


@dataclass
class ExitWorktreeInput:
    """退出工作树输入"""
    keep: bool = False  # 是否保留工作树目录


class EnterWorktreeTool(Tool):
    """
    EnterWorktreeTool - 创建并进入Git工作树

    创建一个新的Git工作树（worktree），用于隔离开发环境。
    工作树允许你在同一仓库中同时处理多个分支，而不需要克隆多个仓库。

    使用场景:
    - 并行开发多个功能
    - 代码审查时保持主分支干净
    - 实验性开发不影响主线
    """

    name = "enter_worktree"
    description = "创建并进入一个新的Git工作树，用于隔离开发环境"
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "工作树名称（可选，默认自动生成）"
            },
            "base_branch": {
                "type": "string",
                "description": "基于哪个分支创建（可选，默认当前分支）"
            }
        }
    }

    async def run(self, input_data: EnterWorktreeInput) -> ToolResult:
        """
        创建并进入工作树

        Args:
            input_data: 工作树输入参数

        Returns:
            ToolResult: 包含工作树信息
        """
        try:
            # 检查是否在git仓库中
            if not self._is_git_repo():
                return ToolResult(
                    success=False,
                    error=ToolError(
                        message="当前目录不是Git仓库",
                        tool_name=self.name
                    )
                )

            # 生成工作树名称
            worktree_name = input_data.name or self._generate_worktree_name()

            # 检查工作树是否已存在
            if self._worktree_exists(worktree_name):
                return ToolResult(
                    success=False,
                    error=ToolError(
                        message=f"工作树 '{worktree_name}' 已存在",
                        tool_name=self.name
                    )
                )

            # 创建工作树路径
            worktree_path = os.path.join(".claude", "worktrees", worktree_name)
            os.makedirs(os.path.dirname(worktree_path), exist_ok=True)

            # 确定基础分支
            base_branch = input_data.base_branch or self._get_current_branch()

            # 创建新分支
            new_branch = f"worktree/{worktree_name}"
            branch_result = subprocess.run(
                ["git", "branch", new_branch, base_branch],
                capture_output=True,
                text=True
            )

            if branch_result.returncode != 0:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        message=f"创建分支失败: {branch_result.stderr}",
                        tool_name=self.name
                    )
                )

            # 创建工作树
            result = subprocess.run(
                ["git", "worktree", "add", worktree_path, new_branch],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                # 清理创建的分支
                subprocess.run(
                    ["git", "branch", "-D", new_branch],
                    capture_output=True
                )
                return ToolResult(
                    success=False,
                    error=ToolError(
                        message=f"创建工作树失败: {result.stderr}",
                        tool_name=self.name
                    )
                )

            # 获取原始目录
            original_path = os.getcwd()

            # 切换到工作树目录
            os.chdir(worktree_path)

            return ToolResult(
                success=True,
                data={
                    "worktree_name": worktree_name,
                    "worktree_path": worktree_path,
                    "branch": new_branch,
                    "base_branch": base_branch,
                    "original_path": original_path
                },
                message=f"已创建工作树: {worktree_name}\n路径: {worktree_path}\n分支: {new_branch}"
            )

        except Exception as e:
            return ToolResult(
                success=False,
                error=ToolError(
                    message=f"创建工作树失败: {str(e)}",
                    tool_name=self.name
                )
            )

    def _is_git_repo(self) -> bool:
        """检查当前目录是否是Git仓库"""
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True
        )
        return result.returncode == 0

    def _get_current_branch(self) -> str:
        """获取当前分支名"""
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True
        )
        return result.stdout.strip() if result.returncode == 0 else "main"

    def _generate_worktree_name(self) -> str:
        """生成工作树名称"""
        import time
        timestamp = int(time.time())
        return f"worktree-{timestamp}"

    def _worktree_exists(self, name: str) -> bool:
        """检查工作树是否已存在"""
        result = subprocess.run(
            ["git", "worktree", "list"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return False
        return name in result.stdout

    def get_schema(self) -> dict:
        """获取工具schema"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class ExitWorktreeTool(Tool):
    """
    ExitWorktreeTool - 退出当前工作树

    退出当前工作树，返回到原始仓库目录。
    可以选择保留或删除工作树。

    使用场景:
    - 完成工作树中的任务
    - 切换回主开发分支
    - 清理临时工作树
    """

    name = "exit_worktree"
    description = "退出当前工作树，返回到原始仓库目录"
    input_schema = {
        "type": "object",
        "properties": {
            "keep": {
                "type": "boolean",
                "description": "是否保留工作树目录（默认删除）",
                "default": False
            }
        }
    }

    async def run(self, input_data: ExitWorktreeInput) -> ToolResult:
        """
        退出工作树

        Args:
            input_data: 退出参数

        Returns:
            ToolResult: 包含退出结果
        """
        try:
            # 检查当前是否在工作树中
            worktree_info = self._get_current_worktree_info()
            if not worktree_info:
                return ToolResult(
                    success=False,
                    error=ToolError(
                        message="当前不在工作树中",
                        tool_name=self.name
                    )
                )

            worktree_path = worktree_info["path"]
            original_path = worktree_info.get("original_path")

            # 如果没有记录原始路径，尝试推断
            if not original_path:
                # 查找主仓库路径
                result = subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    original_path = result.stdout.strip()

            # 切换回原始目录
            if original_path and os.path.exists(original_path):
                os.chdir(original_path)

            # 如果不保留，删除工作树
            if not input_data.keep:
                # 移除工作树
                subprocess.run(
                    ["git", "worktree", "remove", worktree_path],
                    capture_output=True
                )

                # 删除对应分支
                branch = worktree_info.get("branch", "")
                if branch.startswith("worktree/"):
                    subprocess.run(
                        ["git", "branch", "-D", branch],
                        capture_output=True
                    )

            return ToolResult(
                success=True,
                data={
                    "worktree_path": worktree_path,
                    "original_path": original_path,
                    "kept": input_data.keep
                },
                message=f"已退出工作树: {worktree_path}\n" +
                        ("工作树已保留" if input_data.keep else "工作树已删除")
            )

        except Exception as e:
            return ToolResult(
                success=False,
                error=ToolError(
                    message=f"退出工作树失败: {str(e)}",
                    tool_name=self.name
                )
            )

    def _get_current_worktree_info(self) -> Optional[dict]:
        """获取当前工作树信息"""
        try:
            # 获取当前路径
            current_path = os.getcwd()

            # 检查是否在.claude/worktrees下
            if ".claude/worktrees" not in current_path:
                return None

            # 获取工作树列表
            result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                return None

            # 解析工作树信息
            for line in result.stdout.split("\n"):
                if line.startswith("worktree "):
                    path = line[9:]
                    if current_path.startswith(path):
                        return {"path": path}

            return {"path": current_path}

        except Exception:
            return None

    def get_schema(self) -> dict:
        """获取工具schema"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
