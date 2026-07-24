"""Stable markdown persistence for session plans."""

from __future__ import annotations

from pathlib import Path


class PlanFileStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, slug: str) -> Path:
        return self.root / "plans" / f"{slug}.md"

    def save(self, slug: str, content: str) -> str:
        path = self.path_for(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    def load(self, slug: str) -> str | None:
        path = self.path_for(slug)
        return path.read_text(encoding="utf-8") if path.exists() else None
