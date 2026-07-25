"""Shared Agent Skill name and containment rules."""

from __future__ import annotations

import re
from pathlib import Path


SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def validate_skill_name(name: str) -> str:
    """Return a portable skill name or raise ``ValueError``."""
    if (
        not isinstance(name, str)
        or Path(name).is_absolute()
        or "/" in name
        or "\\" in name
        or name in {".", ".."}
        or not SKILL_NAME_PATTERN.fullmatch(name)
        or "--" in name
    ):
        raise ValueError(f"Invalid skill name: {name}")
    return name


def is_valid_skill_name(name: object) -> bool:
    try:
        validate_skill_name(name)  # type: ignore[arg-type]
    except ValueError:
        return False
    return True


def resolve_skill_path(skills_root: Path | str, skill_name: str) -> Path:
    """Resolve a direct child without re-resolving the trusted root itself."""
    name = validate_skill_name(skill_name)
    root = Path(skills_root)
    candidate = (root / name).resolve(strict=False)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Skill path escapes skills directory: {name}") from exc
    if len(relative.parts) != 1 or candidate.name != name:
        raise ValueError(f"Skill path escapes skills directory: {name}")
    return candidate
