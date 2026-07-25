import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
