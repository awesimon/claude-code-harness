# Agent Skills 标准协议实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按照 agentskills.io 标准协议实现动态 skill 安装功能，支持标准的 SKILL.md 格式、目录结构和可选子目录（scripts/、references/、assets/）

**Architecture:** 
1. 创建 `AgentSkillLoader` 类负责从 `.claude/skills/` 目录加载标准格式的 skill
2. 实现 `SKILL.md` YAML frontmatter 解析器
3. 创建 `SkillRegistry` 管理已加载的 skill
4. 实现 `SkillTool` 支持通过工具调用执行 skill
5. 支持可选子目录：scripts/（可执行代码）、references/（参考文档）、assets/（资源文件）

**Tech Stack:** Python, Pydantic（数据验证）, PyYAML（frontmatter 解析）, Markdown（内容解析）

---

## 文件结构

### 新创建的文件

| 文件 | 职责 |
|------|------|
| `services/skill_registry.py` | Skill 注册表，管理所有已加载的 skill |
| `services/skill_loader.py` | Skill 加载器，解析 SKILL.md 和目录结构 |
| `models/skill.py` | Skill 数据模型（Pydantic） |
| `tools/skill_tool_v2.py` | 新版 Skill 工具（符合标准协议） |
| `utils/frontmatter_parser.py` | YAML frontmatter 解析工具 |
| `tests/test_skill_loader.py` | Skill 加载器测试 |
| `tests/test_skill_registry.py` | Skill 注册表测试 |

### 修改的文件

| 文件 | 修改内容 |
|------|----------|
| `tools/__init__.py` | 导出新的 skill 工具 |
| `main.py` | 初始化 skill 加载 |

---

## Task 1: Skill 数据模型

**Files:**
- Create: `models/skill.py`

- [ ] **Step 1: 定义 Skill 基础模型**

```python
"""
Agent Skills 标准协议数据模型
对应 agentskills.io 规范
"""
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, validator
from datetime import datetime
from enum import Enum


class SkillMetadata(BaseModel):
    """Skill 元数据（metadata 字段）"""
    author: Optional[str] = None
    version: Optional[str] = None
    # 允许任意额外字段
    class Config:
        extra = "allow"


class SkillDefinition(BaseModel):
    """
    Skill 定义模型
    对应 SKILL.md 的 frontmatter + 内容
    """
    # 必需字段
    name: str = Field(..., min_length=1, max_length=64, regex=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    description: str = Field(..., min_length=1, max_length=1024)
    
    # 可选字段
    license: Optional[str] = None
    compatibility: Optional[str] = Field(None, max_length=500)
    metadata: Optional[SkillMetadata] = None
    allowed_tools: Optional[List[str]] = None  # 从 allowed-tools 解析
    
    # 运行时字段（非 frontmatter）
    content: str = ""  # Markdown 内容（frontmatter 之后）
    base_dir: Optional[str] = None  # Skill 所在目录
    
    # 可选子目录
    has_scripts: bool = False
    has_references: bool = False
    has_assets: bool = False
    
    # 加载时间戳
    loaded_at: Optional[datetime] = None
    
    @validator('name')
    def validate_name(cls, v):
        """验证 name 字段符合规范"""
        if '--' in v:
            raise ValueError('name cannot contain consecutive hyphens')
        if v.startswith('-') or v.endswith('-'):
            raise ValueError('name cannot start or end with hyphen')
        return v
    
    @validator('allowed_tools', pre=True)
    def parse_allowed_tools(cls, v):
        """解析 allowed-tools（空格分隔的字符串或列表）"""
        if isinstance(v, str):
            return v.split()
        return v
    
    class Config:
        extra = "allow"  # 允许额外字段用于扩展


class SkillExecutionResult(BaseModel):
    """Skill 执行结果"""
    success: bool
    data: Any = None
    message: str = ""
    error: Optional[str] = None
    executed_at: datetime = Field(default_factory=datetime.now)
```

- [ ] **Step 2: Commit**

```bash
git add models/skill.py
git commit -m "feat: add Agent Skills standard protocol data models"
```

---

## Task 2: Frontmatter 解析器

**Files:**
- Create: `utils/frontmatter_parser.py`

- [ ] **Step 1: 实现 YAML Frontmatter 解析**

```python
"""
YAML Frontmatter 解析工具
解析 SKILL.md 文件的 frontmatter + content 结构
"""
import re
from typing import Dict, Any, Tuple, Optional
import yaml


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """
    解析 Markdown 文件的 frontmatter 和正文
    
    格式:
    ---
    key: value
    ---
    # Markdown content...
    
    Returns:
        (frontmatter_dict, markdown_content)
    """
    # 匹配 frontmatter 模式
    pattern = r'^---\s*\n(.*?)\n---\s*\n(.*)$'
    match = re.match(pattern, content, re.DOTALL)
    
    if not match:
        # 没有 frontmatter，返回空 dict 和原内容
        return {}, content.strip()
    
    frontmatter_text = match.group(1)
    markdown_content = match.group(2).strip()
    
    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML frontmatter: {e}")
    
    return frontmatter, markdown_content


def extract_frontmatter_field(frontmatter: Dict[str, Any], field: str, default: Any = None) -> Any:
    """安全地提取 frontmatter 字段（支持连字符和下划线转换）"""
    # 直接匹配
    if field in frontmatter:
        return frontmatter[field]
    
    # 连字符转下划线
    field_with_dashes = field.replace('_', '-')
    if field_with_dashes in frontmatter:
        return frontmatter[field_with_dashes]
    
    return default
```

- [ ] **Step 2: 创建测试**

Create: `tests/test_frontmatter_parser.py`

```python
import pytest
from utils.frontmatter_parser import parse_frontmatter, extract_frontmatter_field


def test_parse_frontmatter_basic():
    content = """---
name: test-skill
description: A test skill
---
# Test Skill

This is the content.
"""
    frontmatter, markdown = parse_frontmatter(content)
    
    assert frontmatter['name'] == 'test-skill'
    assert frontmatter['description'] == 'A test skill'
    assert '# Test Skill' in markdown


def test_parse_frontmatter_no_frontmatter():
    content = "# Just markdown\n\nNo frontmatter here."
    frontmatter, markdown = parse_frontmatter(content)
    
    assert frontmatter == {}
    assert markdown == content.strip()


def test_extract_frontmatter_field():
    frontmatter = {
        'allowed-tools': ['Read', 'Write'],
        'metadata': {'author': 'test'}
    }
    
    assert extract_frontmatter_field(frontmatter, 'allowed_tools') == ['Read', 'Write']
    assert extract_frontmatter_field(frontmatter, 'allowed-tools') == ['Read', 'Write']
    assert extract_frontmatter_field(frontmatter, 'missing', 'default') == 'default'
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_frontmatter_parser.py -v
```
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add utils/frontmatter_parser.py tests/test_frontmatter_parser.py
git commit -m "feat: add YAML frontmatter parser for SKILL.md"
```

---

## Task 3: Skill 加载器

**Files:**
- Create: `services/skill_loader.py`

- [ ] **Step 1: 实现 SkillLoader 类**

```python
"""
Agent Skills 加载器
按照 agentskills.io 标准协议加载 skill
"""
import os
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime
import asyncio

from models.skill import SkillDefinition, SkillMetadata
from utils.frontmatter_parser import parse_frontmatter, extract_frontmatter_field


class SkillLoader:
    """
    Skill 加载器
    
    从 .claude/skills/ 目录加载符合标准协议的 skill
    目录结构:
        skill-name/
        ├── SKILL.md          # 必需
        ├── scripts/          # 可选
        ├── references/       # 可选
        └── assets/           # 可选
    """
    
    def __init__(self, skills_dir: Optional[str] = None):
        """
        初始化加载器
        
        Args:
            skills_dir: Skill 目录路径，默认 ~/.claude/skills
        """
        if skills_dir is None:
            skills_dir = os.path.expanduser("~/.claude/skills")
        self.skills_dir = Path(skills_dir)
    
    def ensure_skills_dir(self) -> None:
        """确保 skills 目录存在"""
        self.skills_dir.mkdir(parents=True, exist_ok=True)
    
    async def load_all_skills(self) -> Dict[str, SkillDefinition]:
        """
        加载所有 skill
        
        Returns:
            Dict[skill_name, SkillDefinition]
        """
        skills = {}
        
        if not self.skills_dir.exists():
            return skills
        
        # 遍历所有子目录
        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            
            try:
                skill = await self.load_skill_from_dir(skill_dir)
                if skill:
                    skills[skill.name] = skill
            except Exception as e:
                print(f"Failed to load skill from {skill_dir}: {e}")
        
        return skills
    
    async def load_skill_from_dir(self, skill_dir: Path) -> Optional[SkillDefinition]:
        """
        从目录加载单个 skill
        
        Args:
            skill_dir: Skill 目录路径
            
        Returns:
            SkillDefinition 或 None（如果不是有效的 skill 目录）
        """
        skill_md_path = skill_dir / "SKILL.md"
        
        if not skill_md_path.exists():
            return None
        
        # 异步读取文件
        content = await asyncio.to_thread(skill_md_path.read_text, encoding='utf-8')
        
        return self._parse_skill(skill_dir, content)
    
    def _parse_skill(self, skill_dir: Path, content: str) -> SkillDefinition:
        """
        解析 skill 内容
        
        Args:
            skill_dir: Skill 目录
            content: SKILL.md 文件内容
            
        Returns:
            SkillDefinition
        """
        # 解析 frontmatter
        frontmatter, markdown_content = parse_frontmatter(content)
        
        # 提取必需字段
        name = frontmatter.get('name')
        if not name:
            raise ValueError(f"Missing required 'name' field in {skill_dir}")
        
        description = frontmatter.get('description')
        if not description:
            raise ValueError(f"Missing required 'description' field in {skill_dir}")
        
        # 验证目录名匹配 name 字段
        if skill_dir.name != name:
            raise ValueError(f"Skill directory name '{skill_dir.name}' does not match 'name' field '{name}'")
        
        # 提取可选字段
        license_field = frontmatter.get('license')
        compatibility = frontmatter.get('compatibility')
        
        # 解析 metadata
        metadata_raw = frontmatter.get('metadata', {})
        metadata = SkillMetadata(**metadata_raw) if metadata_raw else None
        
        # 解析 allowed-tools（支持空格分隔的字符串或列表）
        allowed_tools_raw = extract_frontmatter_field(frontmatter, 'allowed_tools')
        allowed_tools = None
        if allowed_tools_raw:
            if isinstance(allowed_tools_raw, str):
                allowed_tools = allowed_tools_raw.split()
            elif isinstance(allowed_tools_raw, list):
                allowed_tools = allowed_tools_raw
        
        # 检查可选子目录
        has_scripts = (skill_dir / "scripts").is_dir()
        has_references = (skill_dir / "references").is_dir()
        has_assets = (skill_dir / "assets").is_dir()
        
        return SkillDefinition(
            name=name,
            description=description,
            license=license_field,
            compatibility=compatibility,
            metadata=metadata,
            allowed_tools=allowed_tools,
            content=markdown_content,
            base_dir=str(skill_dir.absolute()),
            has_scripts=has_scripts,
            has_references=has_references,
            has_assets=has_assets,
            loaded_at=datetime.now()
        )
    
    def get_skill_path(self, skill_name: str) -> Optional[Path]:
        """获取 skill 目录路径"""
        skill_dir = self.skills_dir / skill_name
        if skill_dir.exists() and skill_dir.is_dir():
            return skill_dir
        return None
    
    async def install_skill(self, source_path: str, skill_name: Optional[str] = None) -> str:
        """
        安装 skill（从本地路径复制）
        
        Args:
            source_path: 源 skill 目录路径
            skill_name: 目标 skill 名称（默认使用源目录名）
            
        Returns:
            安装的 skill 名称
        """
        source = Path(source_path)
        if not source.exists():
            raise ValueError(f"Source path does not exist: {source_path}")
        
        # 验证源目录包含 SKILL.md
        if not (source / "SKILL.md").exists():
            raise ValueError(f"Source is not a valid skill directory (missing SKILL.md): {source_path}")
        
        # 确定目标名称
        if skill_name is None:
            skill_name = source.name
        
        # 验证名称格式
        if not self._is_valid_skill_name(skill_name):
            raise ValueError(f"Invalid skill name: {skill_name}")
        
        # 复制目录
        target = self.skills_dir / skill_name
        if target.exists():
            raise ValueError(f"Skill already exists: {skill_name}")
        
        await asyncio.to_thread(self._copy_tree, source, target)
        
        return skill_name
    
    def _is_valid_skill_name(self, name: str) -> bool:
        """验证 skill 名称格式"""
        if not name:
            return False
        if len(name) > 64:
            return False
        if name.startswith('-') or name.endswith('-'):
            return False
        if '--' in name:
            return False
        # 只允许小写字母、数字和连字符
        return all(c.islower() or c.isdigit() or c == '-' for c in name)
    
    def _copy_tree(self, src: Path, dst: Path) -> None:
        """递归复制目录"""
        import shutil
        shutil.copytree(src, dst)
    
    async def uninstall_skill(self, skill_name: str) -> bool:
        """
        卸载 skill
        
        Args:
            skill_name: Skill 名称
            
        Returns:
            是否成功卸载
        """
        skill_dir = self.skills_dir / skill_name
        if not skill_dir.exists():
            return False
        
        await asyncio.to_thread(self._remove_tree, skill_dir)
        return True
    
    def _remove_tree(self, path: Path) -> None:
        """递归删除目录"""
        import shutil
        shutil.rmtree(path)
```

- [ ] **Step 2: 创建测试**

Create: `tests/test_skill_loader.py`

```python
import pytest
import tempfile
from pathlib import Path
import asyncio

from services.skill_loader import SkillLoader
from models.skill import SkillDefinition


@pytest.fixture
def temp_skills_dir():
    """创建临时 skills 目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def skill_loader(temp_skills_dir):
    """创建 SkillLoader 实例"""
    return SkillLoader(temp_skills_dir)


@pytest.mark.asyncio
async def test_load_skill_from_dir(skill_loader, temp_skills_dir):
    """测试从目录加载 skill"""
    # 创建测试 skill 目录
    skill_dir = Path(temp_skills_dir) / "test-skill"
    skill_dir.mkdir()
    
    # 创建 SKILL.md
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("""---
name: test-skill
description: A test skill for testing
license: MIT
metadata:
  author: test-author
  version: "1.0.0"
allowed-tools: Read Write Bash
---

# Test Skill

This is a test skill.
""")
    
    # 加载 skill
    skill = await skill_loader.load_skill_from_dir(skill_dir)
    
    assert skill is not None
    assert skill.name == "test-skill"
    assert skill.description == "A test skill for testing"
    assert skill.license == "MIT"
    assert skill.metadata.author == "test-author"
    assert skill.allowed_tools == ["Read", "Write", "Bash"]
    assert "# Test Skill" in skill.content


@pytest.mark.asyncio
async def test_load_all_skills(skill_loader, temp_skills_dir):
    """测试加载所有 skills"""
    # 创建两个 test skills
    for name in ["skill-one", "skill-two"]:
        skill_dir = Path(temp_skills_dir) / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"""---
name: {name}
description: Test skill {name}
---

# {name}
""")
    
    skills = await skill_loader.load_all_skills()
    
    assert len(skills) == 2
    assert "skill-one" in skills
    assert "skill-two" in skills


@pytest.mark.asyncio
async def test_install_skill(skill_loader, temp_skills_dir):
    """测试安装 skill"""
    # 创建源 skill
    source_dir = Path(tempfile.mkdtemp()) / "source-skill"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text("""---
name: source-skill
description: Source skill
---

# Source
""")
    
    # 安装
    installed_name = await skill_loader.install_skill(str(source_dir))
    
    assert installed_name == "source-skill"
    assert (Path(temp_skills_dir) / "source-skill" / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_uninstall_skill(skill_loader, temp_skills_dir):
    """测试卸载 skill"""
    # 创建 skill
    skill_dir = Path(temp_skills_dir) / "to-remove"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("""---
name: to-remove
description: To be removed
---
""")
    
    # 卸载
    result = await skill_loader.uninstall_skill("to-remove")
    
    assert result is True
    assert not skill_dir.exists()


def test_is_valid_skill_name(skill_loader):
    """测试 skill 名称验证"""
    assert skill_loader._is_valid_skill_name("valid-skill") is True
    assert skill_loader._is_valid_skill_name("skill123") is True
    assert skill_loader._is_valid_skill_name("-invalid") is False
    assert skill_loader._is_valid_skill_name("invalid-") is False
    assert skill_loader._is_valid_skill_name("invalid--skill") is False
    assert skill_loader._is_valid_skill_name("Invalid") is False  # 大写
    assert skill_loader._is_valid_skill_name("") is False
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_skill_loader.py -v
```
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add services/skill_loader.py tests/test_skill_loader.py
git commit -m "feat: implement Agent Skills loader with standard protocol support"
```

---

## Task 4: Skill 注册表

**Files:**
- Create: `services/skill_registry.py`

- [ ] **Step 1: 实现 SkillRegistry 类**

```python
"""
Skill 注册表
管理所有已加载的 skill，提供查询和执行接口
"""
from typing import Dict, Optional, List, Any, Callable
import asyncio
from dataclasses import dataclass

from models.skill import SkillDefinition, SkillExecutionResult
from services.skill_loader import SkillLoader


@dataclass
class SkillExecutionContext:
    """Skill 执行上下文"""
    session_id: Optional[str] = None
    agent_context: Optional[Any] = None
    tool_context: Optional[Dict[str, Any]] = None


class SkillRegistry:
    """
    Skill 注册表
    
    单例模式管理所有已加载的 skill
    """
    
    _instance: Optional['SkillRegistry'] = None
    _initialized: bool = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if SkillRegistry._initialized:
            return
        
        self._skills: Dict[str, SkillDefinition] = {}
        self._loader: Optional[SkillLoader] = None
        self._executors: Dict[str, Callable] = {}
        SkillRegistry._initialized = True
    
    def initialize(self, skills_dir: Optional[str] = None) -> None:
        """
        初始化注册表
        
        Args:
            skills_dir: Skill 目录路径
        """
        self._loader = SkillLoader(skills_dir)
    
    async def load_all_skills(self) -> int:
        """
        加载所有 skill
        
        Returns:
            加载的 skill 数量
        """
        if not self._loader:
            raise RuntimeError("SkillRegistry not initialized. Call initialize() first.")
        
        skills = await self._loader.load_all_skills()
        self._skills = skills
        return len(skills)
    
    def get(self, name: str) -> Optional[SkillDefinition]:
        """
        获取 skill 定义
        
        Args:
            name: Skill 名称
            
        Returns:
            SkillDefinition 或 None
        """
        return self._skills.get(name)
    
    def list_skills(self) -> List[str]:
        """列出所有已加载的 skill 名称"""
        return list(self._skills.keys())
    
    def get_all_skills(self) -> Dict[str, SkillDefinition]:
        """获取所有 skill 定义"""
        return self._skills.copy()
    
    def register_executor(self, skill_name: str, executor: Callable) -> None:
        """
        为 skill 注册执行器
        
        Args:
            skill_name: Skill 名称
            executor: 执行函数
        """
        self._executors[skill_name] = executor
    
    async def execute_skill(
        self,
        skill_name: str,
        args: Optional[str] = None,
        context: Optional[SkillExecutionContext] = None
    ) -> SkillExecutionResult:
        """
        执行 skill
        
        Args:
            skill_name: Skill 名称
            args: 执行参数
            context: 执行上下文
            
        Returns:
            SkillExecutionResult
        """
        skill = self.get(skill_name)
        if not skill:
            return SkillExecutionResult(
                success=False,
                error=f"Skill not found: {skill_name}"
            )
        
        # 如果有注册的执行器，使用它
        if skill_name in self._executors:
            try:
                result = await self._executors[skill_name](skill, args, context)
                return SkillExecutionResult(success=True, data=result)
            except Exception as e:
                return SkillExecutionResult(success=False, error=str(e))
        
        # 默认执行：返回 skill 内容
        return SkillExecutionResult(
            success=True,
            data={
                "skill": skill_name,
                "description": skill.description,
                "content": skill.content,
                "args": args
            },
            message=f"Skill '{skill_name}' loaded successfully. Use this content to guide your actions."
        )
    
    async def install_skill(self, source_path: str, skill_name: Optional[str] = None) -> str:
        """
        安装 skill
        
        Args:
            source_path: 源路径
            skill_name: 目标名称
            
        Returns:
            安装的 skill 名称
        """
        if not self._loader:
            raise RuntimeError("SkillRegistry not initialized")
        
        name = await self._loader.install_skill(source_path, skill_name)
        
        # 重新加载该 skill
        skill_dir = self._loader.get_skill_path(name)
        if skill_dir:
            skill = await self._loader.load_skill_from_dir(skill_dir)
            if skill:
                self._skills[name] = skill
        
        return name
    
    async def uninstall_skill(self, skill_name: str) -> bool:
        """
        卸载 skill
        
        Args:
            skill_name: Skill 名称
            
        Returns:
            是否成功
        """
        if not self._loader:
            raise RuntimeError("SkillRegistry not initialized")
        
        result = await self._loader.uninstall_skill(skill_name)
        
        if result and skill_name in self._skills:
            del self._skills[skill_name]
        
        return result
    
    def clear(self) -> None:
        """清空注册表（用于测试）"""
        self._skills.clear()
        self._executors.clear()


# 全局注册表实例
def get_skill_registry() -> SkillRegistry:
    """获取全局 SkillRegistry 实例"""
    return SkillRegistry()
```

- [ ] **Step 2: 创建测试**

Create: `tests/test_skill_registry.py`

```python
import pytest
import tempfile
from pathlib import Path

from services.skill_registry import SkillRegistry, get_skill_registry
from models.skill import SkillDefinition


@pytest.fixture
def fresh_registry():
    """提供全新的注册表实例（用于测试）"""
    # 重置单例
    SkillRegistry._instance = None
    SkillRegistry._initialized = False
    
    registry = SkillRegistry()
    yield registry
    
    # 清理
    registry.clear()


@pytest.fixture
def temp_skills_dir():
    """临时 skills 目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.mark.asyncio
async def test_load_all_skills(fresh_registry, temp_skills_dir):
    """测试加载所有 skills"""
    # 创建测试 skill
    skill_dir = Path(temp_skills_dir) / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("""---
name: test-skill
description: Test skill
---

# Test
""")
    
    fresh_registry.initialize(temp_skills_dir)
    count = await fresh_registry.load_all_skills()
    
    assert count == 1
    assert "test-skill" in fresh_registry.list_skills()


def test_get_skill(fresh_registry):
    """测试获取 skill"""
    # 手动添加 skill
    fresh_registry._skills["manual"] = SkillDefinition(
        name="manual",
        description="Manual skill"
    )
    
    skill = fresh_registry.get("manual")
    assert skill is not None
    assert skill.name == "manual"
    
    # 获取不存在的 skill
    assert fresh_registry.get("nonexistent") is None


@pytest.mark.asyncio
async def test_execute_skill(fresh_registry):
    """测试执行 skill"""
    fresh_registry._skills["test"] = SkillDefinition(
        name="test",
        description="Test skill",
        content="# Test Content"
    )
    
    result = await fresh_registry.execute_skill("test", args="hello")
    
    assert result.success is True
    assert result.data["skill"] == "test"
    assert result.data["args"] == "hello"


@pytest.mark.asyncio
async def test_execute_nonexistent_skill(fresh_registry):
    """测试执行不存在的 skill"""
    result = await fresh_registry.execute_skill("nonexistent")
    
    assert result.success is False
    assert "not found" in result.error


def test_singleton():
    """测试单例模式"""
    registry1 = get_skill_registry()
    registry2 = get_skill_registry()
    
    assert registry1 is registry2
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_skill_registry.py -v
```
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add services/skill_registry.py tests/test_skill_registry.py
git commit -m "feat: add SkillRegistry for managing loaded skills"
```

---

## Task 5: 新版 Skill 工具

**Files:**
- Create: `tools/skill_tool_v2.py`

- [ ] **Step 1: 实现新版 Skill 工具**

```python
"""
Agent Skills 工具（标准协议版）
按照 agentskills.io 规范实现
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from datetime import datetime

from tools.base import Tool, ToolResult, ToolError, ToolValidationError, ToolExecutionError, register_tool
from services.skill_registry import get_skill_registry, SkillExecutionContext
from models.skill import SkillExecutionResult


@dataclass
class SkillExecuteInput:
    """执行 skill 的输入"""
    skill: str  # skill 名称
    args: Optional[str] = None  # 可选参数


@dataclass
class SkillListInput:
    """列出 skills 的输入"""
    include_details: bool = False  # 是否包含详细信息


@dataclass
class SkillInstallInput:
    """安装 skill 的输入"""
    source: str  # 源路径（本地目录或 Git URL）
    name: Optional[str] = None  # 可选的目标名称


@dataclass
class SkillUninstallInput:
    """卸载 skill 的输入"""
    skill: str  # skill 名称


@register_tool
class SkillExecuteToolV2(Tool[SkillExecuteInput, Dict[str, Any]]):
    """
    执行 Agent Skill
    
    按照 agentskills.io 标准协议执行指定的 skill。
    Skill 必须已安装在 .claude/skills/ 目录中。
    """
    
    name = "skill_execute"
    description = """Execute an installed Agent Skill.
    
Skills are packages of instructions, scripts, and resources that give agents
capabilities and expertise. They follow the agentskills.io standard protocol.

Example usage:
- skill: pdf-processing, args: extract text from report.pdf
- skill: data-analysis, args: analyze sales_data.csv
"""
    version = "2.0"
    
    def __init__(self):
        super().__init__()
        self._registry = get_skill_registry()
    
    async def validate(self, input_data: SkillExecuteInput) -> Optional[ToolError]:
        """验证输入"""
        if not input_data.skill or not input_data.skill.strip():
            return ToolValidationError("skill name is required")
        
        skill_name = input_data.skill.strip()
        
        # 检查 skill 是否存在
        if not self._registry.get(skill_name):
            available = ", ".join(self._registry.list_skills()[:10])
            return ToolValidationError(
                f"Skill '{skill_name}' not found. "
                f"Available skills: {available}"
            )
        
        return None
    
    async def execute(self, input_data: SkillExecuteInput) -> ToolResult:
        """执行 skill"""
        skill_name = input_data.skill.strip()
        
        try:
            # 执行 skill
            result = await self._registry.execute_skill(
                skill_name=skill_name,
                args=input_data.args,
                context=SkillExecutionContext()
            )
            
            if result.success:
                return ToolResult.ok(
                    data=result.data,
                    message=result.message or f"Successfully executed skill: {skill_name}",
                    metadata={
                        "skill": skill_name,
                        "args": input_data.args,
                        "executed_at": result.executed_at.isoformat()
                    }
                )
            else:
                return ToolResult.error(
                    ToolExecutionError(result.error or "Unknown error")
                )
        
        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"Failed to execute skill: {str(e)}")
            )
    
    def get_schema(self) -> Dict[str, Any]:
        """获取 JSON Schema"""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "Name of the skill to execute"
                    },
                    "args": {
                        "type": "string",
                        "description": "Optional arguments to pass to the skill"
                    }
                },
                "required": ["skill"]
            }
        }


@register_tool
class SkillListToolV2(Tool[SkillListInput, List[Dict[str, Any]]]):
    """
    列出所有已安装的 Agent Skills
    
    返回符合 agentskills.io 标准协议的 skill 列表。
    """
    
    name = "skill_list"
    description = "List all installed Agent Skills with their metadata"
    version = "2.0"
    
    def __init__(self):
        super().__init__()
        self._registry = get_skill_registry()
    
    async def execute(self, input_data: SkillListInput) -> ToolResult:
        """列出所有 skills"""
        try:
            skills = self._registry.get_all_skills()
            
            result = []
            for name, skill in skills.items():
                item = {
                    "name": skill.name,
                    "description": skill.description,
                }
                
                if input_data.include_details:
                    item.update({
                        "license": skill.license,
                        "compatibility": skill.compatibility,
                        "metadata": skill.metadata.dict() if skill.metadata else None,
                        "allowed_tools": skill.allowed_tools,
                        "has_scripts": skill.has_scripts,
                        "has_references": skill.has_references,
                        "has_assets": skill.has_assets,
                        "loaded_at": skill.loaded_at.isoformat() if skill.loaded_at else None
                    })
                
                result.append(item)
            
            # 按名称排序
            result.sort(key=lambda x: x["name"])
            
            return ToolResult.ok(
                data=result,
                message=f"Found {len(result)} installed skills",
                metadata={"count": len(result)}
            )
        
        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"Failed to list skills: {str(e)}")
            )
    
    def is_read_only(self) -> bool:
        return True
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "include_details": {
                        "type": "boolean",
                        "description": "Include detailed metadata for each skill",
                        "default": False
                    }
                }
            }
        }


@register_tool
class SkillInstallToolV2(Tool[SkillInstallInput, str]):
    """
    安装 Agent Skill
    
    从本地路径安装 skill 到 .claude/skills/ 目录。
    """
    
    name = "skill_install"
    description = """Install an Agent Skill from a local directory.
    
The source directory must contain a valid SKILL.md file following
the agentskills.io standard protocol.
"""
    version = "2.0"
    
    def __init__(self):
        super().__init__()
        self._registry = get_skill_registry()
    
    async def validate(self, input_data: SkillInstallInput) -> Optional[ToolError]:
        """验证输入"""
        if not input_data.source or not input_data.source.strip():
            return ToolValidationError("source path is required")
        
        # TODO: 验证路径存在
        
        if input_data.name:
            # 验证名称格式
            name = input_data.name.strip()
            if not self._is_valid_name(name):
                return ToolValidationError(
                    f"Invalid skill name: {name}. "
                    "Must be 1-64 chars, lowercase letters/numbers/hyphens only."
                )
        
        return None
    
    def _is_valid_name(self, name: str) -> bool:
        """验证 skill 名称"""
        if not name or len(name) > 64:
            return False
        if name.startswith('-') or name.endswith('-') or '--' in name:
            return False
        return all(c.islower() or c.isdigit() or c == '-' for c in name)
    
    async def execute(self, input_data: SkillInstallInput) -> ToolResult:
        """安装 skill"""
        try:
            skill_name = await self._registry.install_skill(
                source_path=input_data.source.strip(),
                skill_name=input_data.name.strip() if input_data.name else None
            )
            
            return ToolResult.ok(
                data={"skill": skill_name},
                message=f"Successfully installed skill: {skill_name}",
                metadata={
                    "skill": skill_name,
                    "source": input_data.source,
                    "installed_at": datetime.now().isoformat()
                }
            )
        
        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"Failed to install skill: {str(e)}")
            )
    
    def is_destructive(self) -> bool:
        return True
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Path to the skill directory to install"
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional name for the installed skill (defaults to directory name)"
                    }
                },
                "required": ["source"]
            }
        }


@register_tool
class SkillUninstallToolV2(Tool[SkillUninstallInput, bool]):
    """
    卸载 Agent Skill
    """
    
    name = "skill_uninstall"
    description = "Uninstall an Agent Skill"
    version = "2.0"
    
    def __init__(self):
        super().__init__()
        self._registry = get_skill_registry()
    
    async def validate(self, input_data: SkillUninstallInput) -> Optional[ToolError]:
        """验证输入"""
        if not input_data.skill or not input_data.skill.strip():
            return ToolValidationError("skill name is required")
        
        skill_name = input_data.skill.strip()
        
        # 检查 skill 是否存在
        if not self._registry.get(skill_name):
            return ToolValidationError(f"Skill '{skill_name}' not found")
        
        return None
    
    async def execute(self, input_data: SkillUninstallInput) -> ToolResult:
        """卸载 skill"""
        try:
            skill_name = input_data.skill.strip()
            
            result = await self._registry.uninstall_skill(skill_name)
            
            if result:
                return ToolResult.ok(
                    data=True,
                    message=f"Successfully uninstalled skill: {skill_name}",
                    metadata={"skill": skill_name}
                )
            else:
                return ToolResult.error(
                    ToolExecutionError(f"Failed to uninstall skill: {skill_name}")
                )
        
        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"Failed to uninstall skill: {str(e)}")
            )
    
    def is_destructive(self) -> bool:
        return True
    
    def requires_confirmation(self) -> bool:
        return True
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "Name of the skill to uninstall"
                    }
                },
                "required": ["skill"]
            }
        }
```

- [ ] **Step 2: Commit**

```bash
git add tools/skill_tool_v2.py
git commit -m "feat: implement Agent Skills tools following standard protocol"
```

---

## Task 6: 更新工具导出和初始化

**Files:**
- Modify: `tools/__init__.py`
- Modify: `main.py`

- [ ] **Step 1: 更新 tools/__init__.py**

Add to `tools/__init__.py`:

```python
# Agent Skills v2 (标准协议)
from .skill_tool_v2 import (
    SkillExecuteToolV2,
    SkillListToolV2,
    SkillInstallToolV2,
    SkillUninstallToolV2,
    SkillExecuteInput,
    SkillListInput,
    SkillInstallInput,
    SkillUninstallInput,
)
```

Add to `__all__`:
```python
    # Agent Skills v2
    "SkillExecuteToolV2",
    "SkillListToolV2",
    "SkillInstallToolV2",
    "SkillUninstallToolV2",
    "SkillExecuteInput",
    "SkillListInput",
    "SkillInstallInput",
    "SkillUninstallInput",
```

- [ ] **Step 2: 更新 main.py 初始化 skill**

Add to `main.py` (在应用启动时):

```python
from services.skill_registry import get_skill_registry

# 初始化 Skill Registry
@app.on_event("startup")
async def init_skills():
    """初始化 Agent Skills"""
    registry = get_skill_registry()
    registry.initialize()  # 使用默认 ~/.claude/skills 目录
    count = await registry.load_all_skills()
    print(f"Loaded {count} Agent Skills")
```

- [ ] **Step 3: Commit**

```bash
git add tools/__init__.py main.py
git commit -m "feat: integrate Agent Skills v2 into application"
```

---

## Task 7: 创建示例 Skill

**Files:**
- Create: `examples/skills/hello-world/SKILL.md`
- Create: `examples/skills/hello-world/scripts/hello.py`

- [ ] **Step 1: 创建示例 Skill** 

Create: `examples/skills/hello-world/SKILL.md`

```markdown
---
name: hello-world
description: A simple hello world skill demonstrating the Agent Skills standard protocol
license: MIT
metadata:
  author: Claude Code
  version: "1.0.0"
  category: example
allowed-tools: Bash
---

# Hello World Skill

This is a simple example skill that demonstrates the Agent Skills standard protocol.

## Usage

Use this skill to greet users with a personalized message.

## Examples

- "Say hello to the team"
- "Greet the user with a welcome message"

## Notes

This skill includes an optional scripts/ directory with a Python script that can be executed.
```

Create: `examples/skills/hello-world/scripts/hello.py`

```python
#!/usr/bin/env python3
"""
Hello World script for the hello-world skill
"""
import sys

def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "World"
    print(f"Hello, {name}! Welcome to Agent Skills.")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add examples/skills/hello-world/
git commit -m "docs: add example skill following standard protocol"
```

---

## Task 8: 运行完整测试

- [ ] **Step 1: 运行所有测试**

```bash
pytest tests/test_frontmatter_parser.py tests/test_skill_loader.py tests/test_skill_registry.py -v
```
Expected: All tests pass

- [ ] **Step 2: 验证示例 skill 格式**

```bash
python3 -c "
from services.skill_loader import SkillLoader
import asyncio

loader = SkillLoader('examples/skills')
skills = asyncio.run(loader.load_all_skills())
print(f'Loaded {len(skills)} skills')
for name, skill in skills.items():
    print(f'- {name}: {skill.description}')
"
```
Expected: 
```
Loaded 1 skills
- hello-world: A simple hello world skill...
```

- [ ] **Step 3: Commit 所有更改**

```bash
git add .
git commit -m "feat: complete Agent Skills standard protocol implementation

- Add SKILL.md YAML frontmatter parser
- Implement SkillLoader for loading standard protocol skills
- Add SkillRegistry for managing skills
- Create Skill tools (execute, list, install, uninstall)
- Add example skill following the standard"
```

---

## 验证清单

- [ ] 所有单元测试通过
- [ ] 示例 skill 可以正确加载
- [ ] Skill 工具可以列出已安装的 skills
- [ ] Skill 名称验证符合规范（小写、数字、连字符，1-64字符）
- [ ] Frontmatter 解析正确处理 YAML 和 Markdown 内容
- [ ] 可选子目录（scripts/, references/, assets/）被正确检测
