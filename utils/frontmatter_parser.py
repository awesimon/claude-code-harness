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
