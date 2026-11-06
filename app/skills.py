"""Tenant skill: SKILL.md = YAML frontmatter (model, flags, checks) + the rules text used as the agent instruction."""
import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "skills"


@dataclass(frozen=True)
class Skill:
    name: str
    version: str
    model: str
    flags: list[str]
    checks: dict[str, dict]
    categories: list[str]
    fallback_category: str
    body: str


def parse_skill(text: str) -> Skill:
    _, fm, body = text.split("---\n", 2)
    meta = yaml.safe_load(fm)
    return Skill(meta["name"], hashlib.sha1(text.encode()).hexdigest()[:8], meta["model"], meta["flags"],
                 meta.get("checks", {}), meta.get("categories", []), meta.get("fallback_category", ""), body.strip())


class SkillRegistry:
    """The only way to get a tenant's skill (SPEC §5 seam). Falls back to skills/default."""

    def __init__(self, root: Path = ROOT):
        self.root = root

    def get(self, tenant: str = "default") -> Skill:
        p = self.root / tenant / "SKILL.md"
        return parse_skill((p if p.exists() else self.root / "default" / "SKILL.md").read_text())
