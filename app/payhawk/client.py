"""Payhawk REST v3 client. Points at the built-in mock by default; live differs only in base_url + api_key."""
import json
import os
import time

import httpx

MOCK_BASE_URL = "http://localhost:8080/mock-payhawk/api/v3/accounts/demo_account"


class PayhawkApiError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


class PayhawkClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 http: httpx.Client | None = None, max_retries: int = 3):
        self.base_url = (base_url or os.getenv("PAYHAWK_BASE_URL") or MOCK_BASE_URL).rstrip("/")
        self.http = http or httpx.Client(timeout=30)
        self.http.headers["X-Payhawk-ApiKey"] = api_key or os.getenv("PAYHAWK_API_KEY") or "demo-key"
        self.max_retries = max_retries

    def _send(self, method: str, url: str, **kw) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            r = self.http.request(method, url, **kw)
            if r.status_code == 429 and attempt < self.max_retries:  # 15 req/s limit
                time.sleep(float(r.headers.get("Retry-After", 1)))
                continue
            break
        if r.is_error:
            try:
                e = r.json()
            except ValueError:
                e = {}
            raise PayhawkApiError(r.status_code, e.get("code", "Error"), e.get("message", r.text[:200]))
        return r

    def _req(self, method: str, path: str, **kw):
        r = self._send(method, self.base_url + path, **kw)
        return r.json() if r.content else None

    def _list(self, path: str, flt: dict | None = None, order: list | None = None, take: int = 1000) -> list[dict]:
        """All items of a list endpoint, paging with $skip until `total`."""
        items: list[dict] = []
        while True:
            params = {"$skip": len(items), "$take": take}
            if flt:
                params["$filter"] = json.dumps(flt)
            if order:
                params["$orderBy"] = json.dumps(order)
            page = self._req("GET", path, params=params)
            items += page["items"]
            if not page["items"] or len(items) >= page["total"]:
                return items

    # account / reference data
    def account(self) -> dict:
        return self._req("GET", "")

    def categories(self) -> list[dict]:
        return self._list("/expense-categories")

    def suppliers(self) -> list[dict]:  # the API has no name filter, so match names client-side
        return self._list("/suppliers")

    def create_supplier(self, name: str) -> dict:
        return self._req("POST", "/suppliers", json={"name": name})

    def users(self) -> list[dict]:
        return self._list("/users")

    def custom_fields(self) -> list[dict]:
        return self._list("/custom-fields")

    # expenses
    def expenses(self, flt: dict | None = None, order: list | None = None) -> list[dict]:
        return self._list("/expenses", flt, order)

    def expense(self, expense_id: str) -> dict:
        return self._req("GET", f"/expenses/{expense_id}")

    def files(self, expense_id: str) -> list[dict]:
        return self._req("GET", f"/expenses/{expense_id}/files")["items"]

    def download(self, file: dict) -> bytes:
        return self._send("GET", file["url"]).content

    def payments(self, expense_id: str) -> list[dict]:
        return self._req("GET", f"/expenses/{expense_id}/payments")["items"]

    def workflow(self, expense_id: str) -> dict:
        return self._req("GET", f"/expenses/{expense_id}/workflow")

    # write-back (PATCH works only while the expense is a draft)
    def patch_expense(self, expense_id: str, **fields) -> dict:
        return self._req("PATCH", f"/expenses/{expense_id}", json=fields)

    def set_status(self, expense_id: str, status: str) -> dict:
        return self._req("POST", f"/expenses/{expense_id}/status", json={"status": status})

    def comment(self, expense_id: str, text: str) -> dict:
        return self._req("POST", "/comments", json={"targetType": "expense", "targetId": expense_id, "payload": text})

    def comments(self, expense_id: str) -> list[dict]:
        return self._req("GET", f"/comments/expense/{expense_id}")["items"]

    def set_custom_field(self, expense_id: str, cf_id: str, value: str) -> None:
        self._req("PATCH", f"/expenses/{expense_id}/custom-fields/{cf_id}", json={"value": value})
