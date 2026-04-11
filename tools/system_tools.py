"""
统计和诊断工具
提供系统统计、诊断和帮助功能
"""

import os
import platform
import subprocess
from typing import Optional
from pydantic import BaseModel, Field

from .base import Tool, ToolResult, register_tool


class StatsInput(BaseModel):
    """统计输入"""
    pass


@register_tool
class StatsTool(Tool):
    """显示使用统计"""

    name = "stats"
    description = """显示 Claude Code 的使用统计信息
使用场景：用户说"显示统计"、"使用情况"、"用了多少"等"""
    input_model = StatsInput

    async def execute(self, input_data: StatsInput) -> ToolResult:
        """获取统计"""
        try:
            # 获取工具数量
            from .base import ToolRegistry
            tool_count = len(ToolRegistry.list_tools())

            return ToolResult(
                success=True,
                data={
                    "tools_available": tool_count,
                    "platform": platform.system(),
                    "python_version": platform.python_version()
                },
                message=f"可用工具: {tool_count} 个"
            )

        except Exception as e:
            return ToolResult(
                success=False,
                message="获取统计失败",
                error=str(e)
            )


class DoctorInput(BaseModel):
    """诊断输入"""
    pass


@register_tool
class DoctorTool(Tool):
    """运行系统诊断"""

    name = "doctor"
    description = """诊断 Claude Code 的安装和配置，检查：
- API 密钥配置
- Git 安装
- GitHub CLI 安装
- Python 版本
使用场景：用户说"运行诊断"、"检查配置"、"doctor"等"""
    input_model = DoctorInput

    async def execute(self, input_data: DoctorInput) -> ToolResult:
        """运行诊断"""
        checks = []

        # 检查 API 密钥
        openai_key = bool(os.getenv("OPENAI_API_KEY"))
        anthropic_key = bool(os.getenv("ANTHROPIC_API_KEY"))
        checks.append({
            "name": "API 密钥",
            "status": "pass" if (openai_key or anthropic_key) else "fail",
            "message": "已配置" if (openai_key or anthropic_key) else "未配置"
        })

        # 检查 Git
        try:
            git_version = subprocess.run(
                ["git", "--version"],
                capture_output=True,
                text=True,
                check=True
            )
            checks.append({
                "name": "Git",
                "status": "pass",
                "message": git_version.stdout.strip()
            })
        except:
            checks.append({
                "name": "Git",
                "status": "fail",
                "message": "未安装"
            })

        # 检查 GitHub CLI
        try:
            gh_version = subprocess.run(
                ["gh", "--version"],
                capture_output=True,
                text=True,
                check=True
            )
            checks.append({
                "name": "GitHub CLI",
                "status": "pass",
                "message": gh_version.stdout.split("\n")[0]
            })
        except:
            checks.append({
                "name": "GitHub CLI",
                "status": "warn",
                "message": "未安装（某些功能不可用）"
            })

        passed = sum(1 for c in checks if c["status"] == "pass")
        total = len(checks)

        return ToolResult(
            success=passed == total,
            data={"checks": checks},
            message=f"诊断完成: {passed}/{total} 通过"
        )


class HelpInput(BaseModel):
    """帮助输入"""
    topic: Optional[str] = Field(None, description="帮助主题")


@register_tool
class HelpTool(Tool):
    """显示帮助信息"""

    name = "help"
    description = """显示帮助信息和可用工具列表
使用场景：用户说"帮助"、"help"、"怎么用"等"""
    input_model = HelpInput

    async def execute(self, input_data: HelpInput) -> ToolResult:
        """获取帮助"""
        help_text = """
可用工具类别:

**文件操作**
- Read: 读取文件内容
- Write: 写入文件
- Edit: 编辑文件
- Glob: 搜索文件
- Grep: 搜索内容

**Git 操作**
- git_status: 查看仓库状态
- git_diff: 查看代码差异
- git_commit: 创建提交
- branch_list: 列出分支
- branch_create: 创建分支
- branch_switch: 切换分支
- branch_delete: 删除分支

**PR 操作**
- pr_list: 列出 PR
- pr_view: 查看 PR
- pr_diff: 查看 PR 差异

**会话管理**
- session_save: 保存会话
- session_list: 列出会话
- session_load: 加载会话

**系统**
- Bash: 执行命令
- doctor: 运行诊断
- stats: 使用统计

直接输入你想做的事情，我会自动选择合适的工具。
        """.strip()

        return ToolResult(
            success=True,
            data={"help": help_text},
            message=help_text
        )


class VersionInput(BaseModel):
    """版本输入"""
    pass


@register_tool
class VersionTool(Tool):
    """显示版本信息"""

    name = "version"
    description = """显示 Claude Code 版本信息
使用场景：用户说"版本"、"version"等"""
    input_model = VersionInput

    async def execute(self, input_data: VersionInput) -> ToolResult:
        """获取版本"""
        return ToolResult(
            success=True,
            data={
                "version": "0.3.0",
                "api_version": "v1",
                "platform": platform.system(),
                "python_version": platform.python_version()
            },
            message="Claude Code Python API v0.3.0"
        )
