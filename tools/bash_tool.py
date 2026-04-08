"""
Bash命令执行工具模块
提供安全的shell命令执行功能
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
import asyncio
import shlex

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, ToolTimeoutError, register_tool


@dataclass
class BashInput:
    """Bash命令执行工具的输入参数"""
    command: str
    timeout: Optional[float] = 120.0  # 默认超时120秒
    description: Optional[str] = None  # 命令描述
    working_dir: Optional[str] = None  # 工作目录
    env: Optional[Dict[str, str]] = None  # 环境变量


# 危险命令黑名单
DANGEROUS_COMMANDS = [
    "rm -rf /",
    "rm -rf /*",
    "> /dev/sda",
    "dd if=/dev/zero",
    "mkfs",
    "format",
    "del /f /s /q",
]

# 需要确认的危险命令模式
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+",
    r"dd\s+if=.*of=/dev",
]


@register_tool
class BashTool(Tool[BashInput, Dict[str, Any]]):
    """Bash命令执行工具"""

    name = "bash"
    description = "执行Bash命令，支持超时控制和安全检查。用于运行shell命令、查看目录、安装依赖等。"
    version = "1.0"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的bash命令"
                    },
                    "timeout": {
                        "type": "number",
                        "description": "命令超时时间（秒），默认120秒",
                        "default": 120.0
                    },
                    "description": {
                        "type": "string",
                        "description": "命令的描述（用于日志）",
                        "default": None
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "命令执行的工作目录",
                        "default": None
                    }
                },
                "required": ["command"]
            }
        }

    async def validate(self, input_data: BashInput) -> Optional[ToolError]:
        if not input_data.command or not input_data.command.strip():
            return ToolValidationError("command 不能为空")

        command = input_data.command.strip()

        # 检查黑名单命令
        for dangerous in DANGEROUS_COMMANDS:
            if dangerous in command:
                return ToolPermissionError(f"命令包含危险操作，已被阻止: {dangerous}")

        return None

    async def execute(self, input_data: BashInput) -> ToolResult:
        command = input_data.command.strip()
        timeout = input_data.timeout or 120.0

        try:
            # 创建子进程
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=input_data.working_dir,
                env=input_data.env,
            )

            # 等待执行完成或超时
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                # 超时，终止进程
                try:
                    process.kill()
                    await process.wait()
                except:
                    pass
                return ToolResult.error(
                    ToolTimeoutError(timeout)
                )

            # 解析输出
            stdout_text = stdout.decode('utf-8', errors='replace') if stdout else ""
            stderr_text = stderr.decode('utf-8', errors='replace') if stderr else ""

            result = {
                "stdout": stdout_text,
                "stderr": stderr_text,
                "return_code": process.returncode,
                "command": command,
            }

            if process.returncode == 0:
                return ToolResult.ok(
                    data=result,
                    message=input_data.description or f"命令执行成功",
                    metadata={
                        "return_code": process.returncode,
                        "stdout_length": len(stdout_text),
                        "stderr_length": len(stderr_text),
                    }
                )
            else:
                return ToolResult.ok(
                    data=result,
                    message=f"命令执行完成，返回码: {process.returncode}",
                    metadata={
                        "return_code": process.returncode,
                        "stdout_length": len(stdout_text),
                        "stderr_length": len(stderr_text),
                    }
                )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"执行命令失败: {str(e)}")
            )

    def is_destructive(self) -> bool:
        return True

    def requires_confirmation(self) -> bool:
        return True


@dataclass
class BashBatchInput:
    """批量Bash命令执行工具的输入参数"""
    commands: List[str]
    timeout: Optional[float] = 120.0
    stop_on_error: bool = True


@register_tool
class BashBatchTool(Tool[BashBatchInput, List[Dict[str, Any]]]):
    """批量Bash命令执行工具"""

    name = "bash_batch"
    description = "批量执行多个Bash命令"
    version = "1.0"

    async def validate(self, input_data: BashBatchInput) -> Optional[ToolError]:
        if not input_data.commands:
            return ToolValidationError("commands 不能为空列表")
        return None

    async def execute(self, input_data: BashBatchInput) -> ToolResult:
        results = []
        bash_tool = BashTool()

        for i, command in enumerate(input_data.commands):
            bash_input = BashInput(
                command=command,
                timeout=input_data.timeout,
                description=f"批量命令 [{i+1}/{len(input_data.commands)}]"
            )

            result = await bash_tool.run(bash_input)
            results.append({
                "command": command,
                "success": result.success,
                "data": result.data if result.success else None,
                "error": str(result.error) if result.error else None,
            })

            # 如果出错且要求停止，则中断
            if input_data.stop_on_error and not result.success:
                break

        success_count = sum(1 for r in results if r["success"])

        return ToolResult.ok(
            data=results,
            message=f"批量执行完成: {success_count}/{len(results)} 成功",
            metadata={
                "total_commands": len(input_data.commands),
                "success_count": success_count,
                "failed_count": len(results) - success_count,
            }
        )

    def is_destructive(self) -> bool:
        return True

    def requires_confirmation(self) -> bool:
        return True
