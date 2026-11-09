"""Tenant skill: SKILL.md = YAML frontmatter (model, flags, checks) + the rules text used as the agent instruction."""
import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "skills"
OVERRIDE_ROOT = Path(__file__).resolve().parents[1] / "data" / "skills"  # user-edited Rules; data/ is gitignored


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
    """The only way to get a tenant's skill (SPEC §5 seam). Falls back to skills/default; user edits (Rules) win."""

    def __init__(self, root: Path = ROOT, override_root: Path | None = None):
        self.root, self.override_root = root, override_root or OVERRIDE_ROOT  # resolved at call time so tests can patch it

    def _over(self, tenant: str) -> Path:
        return self.override_root / tenant / "SKILL.md"

    def _base(self, tenant: str) -> Path:
        p = self.root / tenant / "SKILL.md"
        return p if p.exists() else self.root / "default" / "SKILL.md"

    def custom(self, tenant: str = "default") -> bool:
        return self._over(tenant).exists()

    def text(self, tenant: str = "default") -> str:
        return (self._over(tenant) if self.custom(tenant) else self._base(tenant)).read_text()

    def default_text(self, tenant: str = "default") -> str:
        return self._base(tenant).read_text()

    def get(self, tenant: str = "default") -> Skill:
        return parse_skill(self.text(tenant))

    def save(self, tenant: str, text: str) -> None:
        try:
            parse_skill(text)
        except Exception as e:
            raise ValueError(f"Invalid rules: {type(e).__name__}: {e}" if text.count("---\n") >= 2 else "Invalid rules: missing the --- frontmatter block at the top") from e
        self._over(tenant).parent.mkdir(parents=True, exist_ok=True)
        self._over(tenant).write_text(text)

    def reset(self, tenant: str = "default") -> None:
        self._over(tenant).unlink(missing_ok=True)
