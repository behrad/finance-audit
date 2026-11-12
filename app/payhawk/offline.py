"""Offline data source: raw Payhawk expense JSON + receipt files from a local folder; never touches the network.
Layout: <dir>/<id>.json (exactly what GET /expenses/{id} returns) and <dir>/<id>/<fileId>[.ext] (receipt bytes)."""
import json
import os
from pathlib import Path

from app.payhawk.client import PayhawkClient

OFFLINE_DIR = "data/offline/expenses"


class OfflineClient(PayhawkClient):
    def __init__(self, root: str | Path | None = None):  # no super().__init__: no http client, no key
        self.root = Path(root or os.getenv("OFFLINE_DIR") or OFFLINE_DIR)
        self.base_url = f"offline://{self.root}"

    def count(self) -> int:
        return len(list(self.root.glob("*.json")))

    def account(self) -> dict:
        return {"id": "offline", "name": f"Offline ({self.root})"}

    def expenses(self, flt: dict | None = None, order: list | None = None) -> list[dict]:  # filter ignored
        return [json.loads(p.read_text()) for p in sorted(self.root.glob("*.json"))]

    def expense(self, expense_id: str) -> dict:
        return json.loads((self.root / f"{expense_id}.json").read_text())

    def files(self, expense_id: str) -> list[dict]:
        return self.expense(expense_id)["document"]["files"]

    def download(self, file: dict) -> bytes:
        p = next(self.root.glob(f"*/{file['fileId']}*"), None)
        if not p:
            raise FileNotFoundError(f"offline receipt {file['fileId']} not under {self.root}")
        return p.read_bytes()

    # reference data + write-back: local no-ops
    def categories(self): return []
    def suppliers(self): return []
    def users(self): return []
    def custom_fields(self): return []
    def comments(self, expense_id): return []
    def create_supplier(self, name): return {"id": f"offline-{name}", "name": name}
    def patch_expense(self, expense_id, **fields): return {**self.expense(expense_id), **fields}
    def set_status(self, expense_id, status): return {"status": status}
    def comment(self, expense_id, text): return {}
    def set_custom_field(self, expense_id, cf_id, value): return None
