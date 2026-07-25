"""
测试 Skill 安装功能
"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from services.skill_loader import SkillLoader
from services.skill_registry import SkillRegistry, get_skill_registry


async def test_local_install():
    """测试从本地目录安装 skill"""
    print("=" * 60)
    print("测试 1: 本地目录安装")
    print("=" * 60)

    # 初始化 registry
    registry = get_skill_registry()
    registry.initialize()

    # 源 skill 目录
    source_path = "/Users/simon/github/claude-code/python_api/examples/skills/hello-world"

    try:
        # 安装 skill
        skill_name = await registry.install_skill(source_path)
        print(f"✅ 成功安装 skill: {skill_name}")

        # 验证 skill 已加载
        skill = registry.get(skill_name)
        if skill:
            print(f"✅ Skill 已加载到注册表")
            print(f"   名称: {skill.name}")
            print(f"   描述: {skill.description}")
            print(f"   目录: {skill.base_dir}")
        else:
            print("❌ Skill 未在注册表中找到")

        return True

    except Exception as e:
        print(f"❌ 安装失败: {e}")
        return False


async def test_github_install():
    """测试从 GitHub URL 安装 skill"""
    print("\n" + "=" * 60)
    print("测试 2: GitHub URL 安装")
    print("=" * 60)

    # 使用同一个 registry
    registry = get_skill_registry()

    # 测试 GitHub URL - 使用本项目中的示例 skill
    # 注意：这需要 GitHub 可访问
    github_url = "https://github.com/anthropics/claude-code/tree/main/examples/skills/hello-world"

    try:
        # 先卸载之前安装的（如果存在）
        await registry.uninstall_skill("hello-world")
        print("已卸载之前的 hello-world skill")
    except:
        pass

    try:
        # 安装 skill
        skill_name = await registry.install_skill(github_url)
        print(f"✅ 成功从 GitHub 安装 skill: {skill_name}")

        # 验证 skill 已加载
        skill = registry.get(skill_name)
        if skill:
            print(f"✅ Skill 已加载到注册表")
            print(f"   名称: {skill.name}")
            print(f"   描述: {skill.description}")
            print(f"   目录: {skill.base_dir}")
        else:
            print("❌ Skill 未在注册表中找到")

        return True

    except Exception as e:
        print(f"❌ GitHub 安装失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_list_skills():
    """测试列出已安装的 skills"""
    print("\n" + "=" * 60)
    print("测试 3: 列出已安装的 skills")
    print("=" * 60)

    registry = get_skill_registry()

    try:
        # 重新加载所有 skills
        count = await registry.load_all_skills()
        print(f"✅ 加载了 {count} 个 skills")

        # 列出所有 skills
        skills = registry.get_all_skills()
        print(f"\n已安装的 skills:")
        for name, skill in skills.items():
            print(f"  - {name}: {skill.description}")

        return True

    except Exception as e:
        print(f"❌ 列出 skills 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """主测试函数"""
    print("开始测试 Skill 安装功能\n")

    results = []

    # 测试 1: 本地安装
    results.append(("本地安装", await test_local_install()))

    # 测试 2: GitHub 安装
    results.append(("GitHub 安装", await test_github_install()))

    # 测试 3: 列出 skills
    results.append(("列出 skills", await test_list_skills()))

    # 总结
    print("\n" + "=" * 60)
    print("测试结果总结")
    print("=" * 60)
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{name}: {status}")

    # 返回整体结果
    return all(r[1] for r in results)


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
