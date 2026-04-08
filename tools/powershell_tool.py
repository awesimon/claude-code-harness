"""
PowerShellTool - Windows PowerShell命令执行
支持在Windows系统上执行PowerShell命令
"""

import os
import subprocess
import platform
from dataclasses import dataclass, field
from typing import Optional, List

from .base import Tool, ToolResult, ToolError


@dataclass
class PowerShellInput:
    """PowerShell命令输入"""
    command: str
    timeout: float = 120.0
    description: Optional[str] = None
    working_dir: Optional[str] = None
    env: dict = field(default_factory=dict)


class PowerShellTool(Tool):
    """
    PowerShellTool - 在Windows系统上执行PowerShell命令

    用于在Windows环境中执行PowerShell脚本和命令。
    在非Windows系统上会返回错误。

    使用场景:
    - Windows特定的系统管理任务
    - 需要PowerShell特性的脚本执行
    - Windows注册表、WMI操作
    """

    name = "powershell"
    description = "在Windows系统上执行PowerShell命令（仅在Windows上可用）"
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的PowerShell命令"
            },
            "timeout": {
                "type": "number",
                "description": "命令执行超时时间（秒）",
                "default": 120.0
            },
            "description": {
                "type": "string",
                "description": "命令的描述（用于日志）"
            },
            "working_dir": {
                "type": "string",
                "description": "工作目录（可选）"
            },
            "env": {
                "type": "object",
                "description": "额外的环境变量",
                "default": {}
            }
        },
        "required": ["command"]
    }

    def __init__(self):
        self.is_windows = platform.system() == "Windows"

    async def run(self, input_data: PowerShellInput) -> ToolResult:
        """
        执行PowerShell命令

        Args:
            input_data: PowerShell输入参数

        Returns:
            ToolResult: 包含命令执行结果
        """
        # 检查是否在Windows上
        if not self.is_windows:
            return ToolResult(
                success=False,
                error=ToolError(
                    message="PowerShellTool 仅在Windows系统上可用。请使用 BashTool 执行命令。",
                    tool_name=self.name
                )
            )

        try:
            # 准备环境变量
            env = os.environ.copy()
            env.update(input_data.env)

            # 构建命令
            # 使用 -Command 执行命令，-NoProfile 跳过配置文件加载以提高速度
            cmd = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                input_data.command
            ]

            # 执行命令
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=input_data.timeout,
                cwd=input_data.working_dir,
                env=env
            )

            # 构建输出
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"

            return ToolResult(
                success=result.returncode == 0,
                data={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                    "command": input_data.command,
                },
                message=output[:10000] if output else "命令执行完成（无输出）",
                metadata={
                    "returncode": result.returncode,
                    "description": input_data.description,
                }
            )

        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                error=ToolError(
                    message=f"命令执行超时（超过 {input_data.timeout} 秒）",
                    tool_name=self.name
                ),
                metadata={"timeout": input_data.timeout}
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=ToolError(
                    message=f"执行PowerShell命令失败: {str(e)}",
                    tool_name=self.name
                )
            )

    def get_schema(self) -> dict:
        """获取工具schema"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
