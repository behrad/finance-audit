from dataclasses import dataclass, field

from pydantic import BaseModel


class ReceiptExtraction(BaseModel):
    merchant: str
    receipt_number: str
    date: str  # YYYY-MM-DD
    service_start: str = ""
    service_end: str = ""
    total: float
    currency: str
    items: list[str] = []
    legibility: float  # 0..1, how readable the receipt is


class SemanticFlag(BaseModel):
    name: str
    description: str


class Verdict(BaseModel):
    """What the rules LLM may decide: only judgements that need meaning."""
    flags: list[SemanticFlag] = []
    category_fix: str = ""


@dataclass
class Fact:
    flag: str
    detail: str
    fix: dict = field(default_factory=dict)
    duplicate_of: str = ""
