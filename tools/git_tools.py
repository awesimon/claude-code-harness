"""
Git 工具集
提供 git 相关功能，集成到对话流中
"""

import subprocess
from typing import Optional
from pydantic import BaseModel, Field

from .base import Tool, ToolResult, register_tool


class GitStatusInput(BaseModel):
    """Git 状态输入"""
    pass


@register_tool
class GitStatusTool(Tool):
    """查看 Git 仓库状态"""

    name = "git_status"
    description = """查看当前 Git 仓库的状态，包括：
- 当前分支
- 修改的文件
- 暂存的更改
- 未跟踪的文件
使用场景：用户想了解当前代码状态、准备提交前检查"""
    input_model = GitStatusInput

    async def execute(self, input_data: GitStatusInput) -> ToolResult:
        """获取 git 状态"""
        try:
            # 获取状态
            status_result = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True,
                text=True,
                check=True
            )

            # 获取分支
            branch_result = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True,
                text=True,
                check=True
            )

            # 获取最近提交
            log_result = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                capture_output=True,
                text=True,
                check=True
            )

            return ToolResult(
                success=True,
                data={
                    "branch": branch_result.stdout.strip(),
                    "status": status_result.stdout.strip() or "工作目录干净",
                    "recent_commits": log_result.stdout.strip().split("\n")
                },
                message=f"当前分支: {branch_result.stdout.strip()}"
            )

        except subprocess.CalledProcessError as e:
            return ToolResult(
                success=False,
                message="获取 git 状态失败",
                error=str(e)
            )


class GitDiffInput(BaseModel):
    """Git diff 输入"""
    staged: bool = Field(False, description="查看已暂存的更改")


@register_tool
class GitDiffTool(Tool):
    """查看 Git 差异"""

    name = "git_diff"
    description = """查看代码更改的差异，包括：
- 已暂存的更改 (--staged)
- 未暂存的更改
使用场景：用户想查看具体修改了什么内容、代码审查前检查更改"""
    input_model = GitDiffInput

    async def execute(self, input_data: GitDiffInput) -> ToolResult:
        """获取 git diff"""
        try:
            cmd = ["git", "diff"]
            if input_data.staged:
                cmd.append("--staged")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            diff_content = result.stdout.strip()

            if not diff_content:
                return ToolResult(
                    success=True,
                    data={"diff": None},
                    message="没有更改" + ("（已暂存）" if input_data.staged else "")
                )

            return ToolResult(
                success=True,
                data={
                    "diff": diff_content[:5000],  # 限制大小
                    "is_truncated": len(diff_content) > 5000
                },
                message=f"{'已暂存' if input_data.staged else '未暂存'}的更改"
            )

        except subprocess.CalledProcessError as e:
            return ToolResult(
                success=False,
                message="获取 git diff 失败",
                error=str(e)
            )


class GitCommitInput(BaseModel):
    """Git 提交输入"""
    message: Optional[str] = Field(None, description="提交信息，如不提供则自动生成")
    auto_stage: bool = Field(True, description="是否自动暂存所有更改")


@register_tool
class GitCommitTool(Tool):
    """创建 Git 提交"""

    name = "git_commit"
    description = """创建 git 提交，支持：
- 自动生成提交信息
- 自动暂存所有更改
- 遵循 Git 安全协议
使用场景：用户说"提交代码"、"commit"、"保存更改"等"""
    input_model = GitCommitInput

    async def execute(self, input_data: GitCommitInput) -> ToolResult:
        """执行 git 提交"""
        try:
            # 检查状态
            status_result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True
            )

            if not status_result.stdout.strip():
                return ToolResult(
                    success=False,
                    message="没有要提交的更改",
                    error="工作目录干净"
                )

            # 解析更改
            changes = self._parse_changes(status_result.stdout)

            # 自动暂存
            if input_data.auto_stage:
                subprocess.run(
                    ["git", "add", "-A"],
                    capture_output=True,
                    check=True
                )

            # 生成提交信息
            commit_message = input_data.message or self._generate_message(changes)

            # 执行提交
            result = subprocess.run(
                ["git", "commit", "-m", commit_message],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                return ToolResult(
                    success=False,
                    message="提交失败",
                    error=result.stderr
                )

            # 获取提交哈希
            hash_result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                check=True
            )

            return ToolResult(
                success=True,
                data={
                    "commit_hash": hash_result.stdout.strip(),
                    "message": commit_message
                },
                message=f"已创建提交: {commit_message}"
            )

        except subprocess.CalledProcessError as e:
            return ToolResult(
                success=False,
                message="Git 命令执行失败",
                error=str(e)
            )

    def _parse_changes(self, status: str) -> dict:
        """解析更改"""
        changes = {"added": 0, "modified": 0, "deleted": 0}
        for line in status.strip().split("\n"):
            if not line:
                continue
            status_code = line[:2]
            if "A" in status_code:
                changes["added"] += 1
            elif "M" in status_code:
                changes["modified"] += 1
            elif "D" in status_code:
                changes["deleted"] += 1
        return changes

    def _generate_message(self, changes: dict) -> str:
        """生成提交信息"""
        if changes["added"] and not changes["modified"]:
            return f"feat: add {changes['added']} new file(s)"
        elif changes["deleted"] and not changes["modified"]:
            return f"remove: delete {changes['deleted']} file(s)"
        else:
            total = changes["added"] + changes["modified"] + changes["deleted"]
            return f"update: modify {total} file(s)"
