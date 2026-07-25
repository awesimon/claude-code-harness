import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import tempfile
from pathlib import Path

# 直接导入模块，避免 services/__init__.py 的依赖问题
import importlib.util
spec = importlib.util.spec_from_file_location("skill_loader", os.path.join(os.path.dirname(os.path.dirname(__file__)), "services", "skill_loader.py"))
skill_loader_module = importlib.util.module_from_spec(spec)
sys.modules["skill_loader"] = skill_loader_module
spec.loader.exec_module(skill_loader_module)
SkillLoader = skill_loader_module.SkillLoader



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


@pytest.mark.asyncio
@pytest.mark.parametrize("malicious_name", ["../outside", ".", "/absolute-target"])
async def test_uninstall_rejects_paths_outside_the_skills_root(
    tmp_path: Path,
    malicious_name: str,
):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    if malicious_name == ".":
        protected_dir = skills_dir
    elif malicious_name.startswith("/"):
        protected_dir = tmp_path / "absolute-target"
        malicious_name = str(protected_dir)
    else:
        protected_dir = tmp_path / "outside"
    protected_dir.mkdir(exist_ok=True)
    marker = protected_dir / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    loader = SkillLoader(str(skills_dir))

    with pytest.raises(ValueError, match="Invalid skill name"):
        await loader.uninstall_skill(malicious_name)

    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_uninstall_rejects_canonical_path_outside_skills_root(
    tmp_path: Path,
):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    (skills_dir / "linked-skill").symlink_to(outside, target_is_directory=True)
    loader = SkillLoader(str(skills_dir))

    with pytest.raises(ValueError, match="escapes skills directory"):
        await loader.uninstall_skill("linked-skill")

    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_github_install_rejects_frontmatter_name_outside_skills_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    skills_dir = tmp_path / "skills"
    outside = tmp_path / "outside"
    loader = SkillLoader(str(skills_dir))
    skill_document = """---
name: ../outside
description: Must stay inside the skills root
---

# Unsafe skill
"""

    async def fake_download(url: str):
        return skill_document if url.endswith("/SKILL.md") else None

    monkeypatch.setattr(loader, "_download_file", fake_download)

    with pytest.raises(ValueError, match="Invalid SKILL.md content"):
        await loader.install_skill(
            "https://github.com/example/repository/tree/main/reviewing"
        )

    assert not outside.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["install", "uninstall", "get", "load_all"])
async def test_operations_reject_replaced_skills_root(
    tmp_path: Path,
    operation: str,
):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    loader = SkillLoader(str(skills_dir))
    trusted_root = tmp_path / "trusted-root"
    skills_dir.rename(trusted_root)
    outside = tmp_path / "outside"
    outside.mkdir()
    external_skill = outside / "visible-skill"
    external_skill.mkdir()
    marker = external_skill / "SKILL.md"
    marker.write_text(
        "---\nname: visible-skill\ndescription: external\n---\n\nExternal",
        encoding="utf-8",
    )
    skills_dir.symlink_to(outside, target_is_directory=True)
    source = tmp_path / "new-skill"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: new-skill\ndescription: local\n---\n\nLocal",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="skills root changed"):
        if operation == "install":
            await loader.install_skill(str(source))
        elif operation == "uninstall":
            await loader.uninstall_skill("visible-skill")
        elif operation == "get":
            loader.get_skill_path("visible-skill")
        else:
            await loader.load_all_skills()

    assert marker.exists()
    assert not (outside / "new-skill").exists()
    assert not (trusted_root / "new-skill").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_source", ["frontmatter", "name-mismatch", "resource-link"])
async def test_local_install_validates_complete_skill_before_publishing(
    tmp_path: Path,
    invalid_source: str,
):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    target_name = "reviewing"
    if invalid_source == "frontmatter":
        document = "---\nname: reviewing\ndescription: [invalid\n---\n\nReview"
    elif invalid_source == "name-mismatch":
        document = "---\nname: other-skill\ndescription: mismatch\n---\n\nReview"
    else:
        document = "---\nname: reviewing\ndescription: linked\n---\n\nReview"
    (source / "SKILL.md").write_text(document, encoding="utf-8")
    if invalid_source == "resource-link":
        outside = tmp_path / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        references = source / "references"
        references.mkdir()
        (references / "escape.txt").symlink_to(outside)
    loader = SkillLoader(str(skills_dir))

    with pytest.raises(ValueError):
        await loader.install_skill(str(source), target_name)

    assert not (skills_dir / target_name).exists()


def _write_external_skill_document(path: Path, name: str) -> None:
    path.write_text(
        f"---\nname: {name}\ndescription: external content\n---\n\nExternal",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_load_skill_rejects_skill_document_symlink_outside_root(
    tmp_path: Path,
):
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "linked-skill"
    skill_dir.mkdir(parents=True)
    outside_document = tmp_path / "outside-skill.md"
    _write_external_skill_document(outside_document, "linked-skill")
    (skill_dir / "SKILL.md").symlink_to(outside_document)
    loader = SkillLoader(str(skills_dir))

    with pytest.raises(ValueError, match="SKILL.md escapes skill directory"):
        await loader.load_skill_from_dir(skill_dir)


@pytest.mark.asyncio
async def test_load_all_skips_skill_document_symlink_outside_root(
    tmp_path: Path,
):
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "linked-skill"
    skill_dir.mkdir(parents=True)
    outside_document = tmp_path / "outside-skill.md"
    _write_external_skill_document(outside_document, "linked-skill")
    (skill_dir / "SKILL.md").symlink_to(outside_document)
    loader = SkillLoader(str(skills_dir))

    assert await loader.load_all_skills() == {}
