"""Review state. In-memory only; swap for Firestore (P3) behind the same attributes.
shortcut: state is lost on restart, which is consistent with the in-memory mock Payhawk; do it with Firestore before live."""
from dataclasses import dataclass, field

from app.pipeline.checks import Ctx
from datetime import date


@dataclass
class Store:
    reviews: dict[str, dict] = field(default_factory=dict)
    expenses: dict[str, dict] = field(default_factory=dict)   # last seen Payhawk snapshot per expense
    sigs: dict[str, tuple] = field(default_factory=dict)      # expense_id -> (modifiedAt, file ids) of last review
    comment_hashes: dict[str, set] = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)             # write-back log
    runs: list[dict] = field(default_factory=list)
    ctx: Ctx = field(default_factory=lambda: Ctx(date.today()))  # holds duplicate fingerprints for the whole history


def is_manual(source: str) -> bool:
    return bool(source) and not source.startswith("auto_")
