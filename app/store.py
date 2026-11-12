"""Review state. In-memory only; swap for Firestore (P3) behind the same attributes.
shortcut: state is lost on restart, which is consistent with the in-memory mock Payhawk; do it with Firestore before live."""
import os
from dataclasses import dataclass, field

from app.pipeline.checks import Ctx
from datetime import date


@dataclass
class Store:
    reviews: dict[str, dict] = field(default_factory=dict)
    expenses: dict[str, dict] = field(default_factory=dict)   # last seen Payhawk snapshot per expense
    sigs: dict[str, tuple] = field(default_factory=dict)      # expense_id -> (modifiedAt, file ids) of last analysis
    feed_sigs: dict[str, tuple] = field(default_factory=dict)  # same, as last fetched into the Feed
    failed: dict[str, str] = field(default_factory=dict)      # expense_id -> last analyze error
    poll_limit: int | None = field(default_factory=lambda: int(os.getenv("POLL_LIMIT") or 0) or None)  # None = no cap
    auto_poll: bool = False                                   # background timer fetches only when True
    fetch_truncated: bool = False                             # last fetch hit poll_limit
    comment_hashes: dict[str, set] = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)             # write-back log
    runs: list[dict] = field(default_factory=list)
    poll_logs: list[dict] = field(default_factory=list)       # last 10 polls: what Payhawk returned + LLM trace per expense
    ctx: Ctx = field(default_factory=lambda: Ctx(date.today()))  # holds duplicate fingerprints for the whole history


def is_manual(source: str) -> bool:
    return bool(source) and not source.startswith("auto_")
