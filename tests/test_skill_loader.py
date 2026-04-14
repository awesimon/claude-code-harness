import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import tempfile
from pathlib import Path
import asyncio

# 直接导入模块，避免 services/__init__.py 的依赖问题
import importlib.util
spec = importlib.util.spec_from_file_location("skill_loader", os.path.join(os.path.dirname(os.path.dirname(__file__)), "services", "skill_loader.py"))
skill_loader_module = importlib.util.module_from_spec(spec)
sys.modules["skill_loader"] = skill_loader_module
spec.loader.exec_module(skill_loader_module)
SkillLoader = skill_loader_module.SkillLoader

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
