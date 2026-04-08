"""
技能系统工具模块
提供技能加载、执行和管理功能
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable, Awaitable
import json
import os
import importlib.util
import sys
import asyncio
from datetime import datetime

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool


# 全局技能注册表
_global_skills: Dict[str, Dict[str, Any]] = {}


def get_skill_registry() -> Dict[str, Dict[str, Any]]:
    """获取全局技能注册表"""
    return _global_skills


def clear_skill_registry() -> None:
    """清除技能注册表（用于测试）"""
    _global_skills.clear()


@dataclass
class SkillExecuteInput:
    """执行技能的输入参数"""
    skill: str  # 技能名称
    args: Optional[str] = None  # 技能参数（可选）


@dataclass
class SkillListInput:
    """列出技能的输入参数"""
    include_builtin: bool = True  # 是否包含内置技能
    include_custom: bool = True  # 是否包含自定义技能


@dataclass
class SkillInfo:
    """技能信息"""
    name: str
    description: str
    version: str
    builtin: bool  # 是否为内置技能
    path: Optional[str] = None  # 技能文件路径
    author: Optional[str] = None
    created_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class SkillLoader:
    """技能加载器 - 负责从.claude/skills/目录加载技能"""

    def __init__(self):
        self.claude_dir = Path.home() / ".claude"
        self.skills_dir = self.claude_dir / "skills"
        self._builtin_skills: Dict[str, Dict[str, Any]] = {}
        self._custom_skills: Dict[str, Dict[str, Any]] = {}

    def ensure_skills_dir(self) -> None:
        """确保技能目录存在"""
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    async def load_all_skills(self) -> Dict[str, SkillInfo]:
        """加载所有技能（内置+自定义）"""
        skills = {}

        # 加载内置技能
        builtin_skills = await self._load_builtin_skills()
        skills.update(builtin_skills)

        # 加载自定义技能
        custom_skills = await self._load_custom_skills()
        skills.update(custom_skills)

        # 更新全局注册表
        _global_skills.update({name: info.__dict__ for name, info in skills.items()})

        return skills

    async def _load_builtin_skills(self) -> Dict[str, SkillInfo]:
        """加载内置技能"""
        # 内置技能是代码中预定义的简单技能
        builtin_skills = {
            "hello": SkillInfo(
                name="hello",
                description="简单的问候技能，返回欢迎消息",
                version="1.0",
                builtin=True,
                metadata={"example": "skill: hello, args: world"}
            ),
            "time": SkillInfo(
                name="time",
                description="获取当前时间的技能",
                version="1.0",
                builtin=True,
                metadata={"format": "ISO 8601"}
            ),
            "echo": SkillInfo(
                name="echo",
                description="回显输入参数的技能",
                version="1.0",
                builtin=True,
                metadata={"example": "skill: echo, args: hello world"}
            ),
        }
        return builtin_skills

    async def _load_custom_skills(self) -> Dict[str, SkillInfo]:
        """从.claude/skills/目录加载自定义技能"""
        skills = {}

        if not self.skills_dir.exists():
            return skills

        # 遍历技能目录
        for skill_file in self.skills_dir.glob("*.py"):
            try:
                skill_info = await self._load_skill_from_file(skill_file)
                if skill_info:
                    skills[skill_info.name] = skill_info
            except Exception as e:
                print(f"加载技能文件失败 {skill_file}: {e}")

        return skills

    async def _load_skill_from_file(self, file_path: Path) -> Optional[SkillInfo]:
        """从文件加载单个技能"""
        try:
            # 读取技能文件
            content = await asyncio.to_thread(file_path.read_text, encoding='utf-8')

            # 解析技能元数据（从文件顶部的 docstring 或注释中提取）
            lines = content.split('\n')

            # 默认技能信息
            skill_name = file_path.stem
            description = f"自定义技能: {skill_name}"
            version = "1.0"
            author = None

            # 尝试从文件内容提取元数据
            for line in lines[:20]:  # 检查前20行
                line = line.strip()
                if line.startswith('"""') or line.startswith("'''"):
                    # 可能是 docstring 的开始
                    pass
                elif ':' in line and not line.startswith('#'):
                    # 尝试解析键值对
                    if 'description' in line.lower():
                        description = line.split(':', 1)[1].strip().strip('"\'')
                    elif 'version' in line.lower():
                        version = line.split(':', 1)[1].strip().strip('"\'')
                    elif 'author' in line.lower():
                        author = line.split(':', 1)[1].strip().strip('"\'')

            # 获取文件修改时间
            stat = await asyncio.to_thread(file_path.stat)
            created_at = datetime.fromtimestamp(stat.st_mtime).isoformat()

            skill_info = SkillInfo(
                name=skill_name,
                description=description,
                version=version,
                builtin=False,
                path=str(file_path),
                author=author,
                created_at=created_at,
                metadata={"file_size": stat.st_size}
            )

            return skill_info

        except Exception as e:
            print(f"解析技能文件失败 {file_path}: {e}")
            return None

    def get_skill_path(self, skill_name: str) -> Optional[Path]:
        """获取技能文件路径"""
        skill_file = self.skills_dir / f"{skill_name}.py"
        if skill_file.exists():
            return skill_file
        return None


class SkillExecutor:
    """技能执行器 - 负责执行技能"""

    def __init__(self, loader: SkillLoader):
        self.loader = loader

    async def execute(self, skill_name: str, args: Optional[str] = None) -> Dict[str, Any]:
        """执行技能"""
        # 检查是否为内置技能
        if skill_name in ["hello", "time", "echo"]:
            return await self._execute_builtin_skill(skill_name, args)

        # 执行自定义技能
        return await self._execute_custom_skill(skill_name, args)

    async def _execute_builtin_skill(self, skill_name: str, args: Optional[str] = None) -> Dict[str, Any]:
        """执行内置技能"""
        if skill_name == "hello":
            name = args or "World"
            return {
                "message": f"Hello, {name}! Welcome to Claude Code Python API.",
                "skill": skill_name,
                "args": args
            }

        elif skill_name == "time":
            from datetime import datetime
            now = datetime.now()
            return {
                "datetime": now.isoformat(),
                "timestamp": now.timestamp(),
                "formatted": now.strftime("%Y-%m-%d %H:%M:%S"),
                "skill": skill_name
            }

        elif skill_name == "echo":
            return {
                "input": args,
                "output": args or "",
                "skill": skill_name
            }

        else:
            raise ValueError(f"未知内置技能: {skill_name}")

    async def _execute_custom_skill(self, skill_name: str, args: Optional[str] = None) -> Dict[str, Any]:
        """执行自定义技能（从文件加载并执行）"""
        skill_path = self.loader.get_skill_path(skill_name)

        if not skill_path:
            raise ToolExecutionError(f"技能未找到: {skill_name}")

        try:
            # 读取技能文件内容
            content = await asyncio.to_thread(skill_path.read_text, encoding='utf-8')

            # 创建一个安全的执行环境
            skill_globals = {
                "__builtins__": {
                    "print": print,
                    "str": str,
                    "int": int,
                    "float": float,
                    "bool": bool,
                    "list": list,
                    "dict": dict,
                    "len": len,
                    "range": range,
                    "enumerate": enumerate,
                    "zip": zip,
                    "map": map,
                    "filter": filter,
                    "sum": sum,
                    "min": min,
                    "max": max,
                    "abs": abs,
                    "round": round,
                    "type": type,
                    "isinstance": isinstance,
                    "hasattr": hasattr,
                    "getattr": getattr,
                    "setattr": setattr,
                },
                "args": args or "",
                "result": None,
            }

            skill_locals = {}

            # 执行技能代码
            exec(content, skill_globals, skill_locals)

            # 获取执行结果
            result = skill_locals.get("result") or skill_globals.get("result")

            # 如果技能定义了 run 函数，调用它
            if "run" in skill_locals and callable(skill_locals["run"]):
                run_func = skill_locals["run"]
                if asyncio.iscoroutinefunction(run_func):
                    result = await run_func(args or "")
                else:
                    result = run_func(args or "")

            return {
                "skill": skill_name,
                "args": args,
                "result": result,
                "executed_at": datetime.now().isoformat()
            }

        except Exception as e:
            raise ToolExecutionError(f"执行技能失败: {str(e)}")


@register_tool
class SkillExecuteTool(Tool[SkillExecuteInput, Dict[str, Any]]):
    """
    技能执行工具

    执行指定的技能，支持内置技能和从.claude/skills/目录加载的自定义技能。

    内置技能:
    - hello: 简单的问候技能
    - time: 获取当前时间
    - echo: 回显输入参数

    自定义技能:
    放置在 ~/.claude/skills/ 目录下的 Python 文件
    """

    name = "skill"
    description = "执行指定的技能，支持内置技能和自定义技能"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._loader = SkillLoader()
        self._executor = SkillExecutor(self._loader)

    async def validate(self, input_data: SkillExecuteInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.skill or not input_data.skill.strip():
            return ToolValidationError("skill 名称不能为空")

        skill_name = input_data.skill.strip()

        # 检查是否为内置技能
        builtin_skills = {"hello", "time", "echo"}
        if skill_name in builtin_skills:
            return None

        # 检查是否为自定义技能
        skill_path = self._loader.get_skill_path(skill_name)
        if not skill_path:
            return ToolValidationError(
                f"技能 '{skill_name}' 未找到。可用内置技能: {', '.join(builtin_skills)}"
            )

        return None

    async def execute(self, input_data: SkillExecuteInput) -> ToolResult:
        """执行技能"""
        skill_name = input_data.skill.strip()
        args = input_data.args

        try:
            result = await self._executor.execute(skill_name, args)

            return ToolResult.ok(
                data=result,
                message=f"成功执行技能: {skill_name}",
                metadata={
                    "skill": skill_name,
                    "args": args,
                    "executed_at": datetime.now().isoformat()
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"执行技能失败: {str(e)}")
            )

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "技能名称（内置技能: hello, time, echo；或自定义技能文件名）"
                },
                "args": {
                    "type": "string",
                    "description": "传递给技能的参数（可选）"
                }
            },
            "required": ["skill"]
        }
        schema["returns"] = {
            "type": "object",
            "description": "技能执行结果"
        }
        return schema


@register_tool
class SkillListTool(Tool[SkillListInput, List[Dict[str, Any]]]):
    """
    技能列表工具

    列出所有可用的技能，包括内置技能和自定义技能。
    """

    name = "skill_list"
    description = "列出所有可用的技能（内置技能和自定义技能）"
    version = "1.0"

    def __init__(self):
        super().__init__()
        self._loader = SkillLoader()

    async def execute(self, input_data: SkillListInput) -> ToolResult:
        """执行列出技能操作"""
        try:
            # 加载所有技能
            all_skills = await self._loader.load_all_skills()

            # 过滤技能
            filtered_skills = []
            for name, info in all_skills.items():
                if info.builtin and not input_data.include_builtin:
                    continue
                if not info.builtin and not input_data.include_custom:
                    continue
                filtered_skills.append({
                    "name": info.name,
                    "description": info.description,
                    "version": info.version,
                    "builtin": info.builtin,
                    "path": info.path,
                    "author": info.author,
                    "created_at": info.created_at,
                })

            # 按内置技能优先排序
            filtered_skills.sort(key=lambda x: (not x["builtin"], x["name"]))

            return ToolResult.ok(
                data=filtered_skills,
                message=f"找到 {len(filtered_skills)} 个技能",
                metadata={
                    "count": len(filtered_skills),
                    "builtin_count": sum(1 for s in filtered_skills if s["builtin"]),
                    "custom_count": sum(1 for s in filtered_skills if not s["builtin"]),
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"列出技能失败: {str(e)}")
            )

    def is_read_only(self) -> bool:
        return True

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的 JSON Schema 描述"""
        schema = super().get_schema()
        schema["parameters"] = {
            "type": "object",
            "properties": {
                "include_builtin": {
                    "type": "boolean",
                    "description": "是否包含内置技能",
                    "default": True
                },
                "include_custom": {
                    "type": "boolean",
                    "description": "是否包含自定义技能",
                    "default": True
                }
            }
        }
        schema["returns"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "version": {"type": "string"},
                    "builtin": {"type": "boolean"},
                    "path": {"type": "string"},
                    "author": {"type": "string"},
                    "created_at": {"type": "string"}
                }
            }
        }
        return schema
