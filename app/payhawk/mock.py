"""Mock Payhawk v3 API, mounted at /mock-payhawk. Same paths, envelopes, errors and
business rules as api.payhawk.com (checked by tests/test_mock_contract.py).
Mock-only controls: __log, __reset, __arrive, __edit."""
import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PREFIX = "/mock-payhawk"
FIXTURE_BASE = "https://api.payhawk.com/api/v3/accounts/demo_account"
MAX_TAKE = 10000  # spec maximum for $take


class PayhawkError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        self.status, self.code, self.message = status, code, message


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self):
        self.reset()

    def reset(self, released: int | None = None):
        self.ref = json.loads((FIXTURES / "payhawk.json").read_text())
        self.expenses = json.loads((FIXTURES / "expenses.json").read_text())["items"]
        self.released = len(self.expenses) if released is None else released
        self.comments: list[dict] = []
        self.log: list[dict] = []
        self._seq = 1000

    def next_id(self) -> str:
        self._seq += 1
        return str(self._seq)

    def visible(self) -> list[dict]:
        return self.expenses[: self.released]

    def expense(self, eid: str) -> dict:
        for e in self.visible():
            if e["id"] == eid:
                return e
        raise PayhawkError(404, "NotFound", "Expense not found")


store = Store()


# ---- request/response plumbing -------------------------------------------------
class MockRoute(APIRoute):
    """Real-API error bodies ({code, message}) and a log of every write call."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(req: Request):
            try:
                resp = await handler(req)
            except PayhawkError as e:
                resp = JSONResponse({"code": e.code, "message": e.message}, e.status)
            except RequestValidationError as e:
                resp = JSONResponse({"code": "BadRequest", "message": str(e.errors())[:500]}, 400)
            if req.method != "GET" and "/__" not in req.url.path:
                try:
                    body = json.loads(await req.body() or "null")
                except ValueError:
                    body = None
                store.log.append({"ts": now(), "method": req.method, "path": req.url.path,
                                  "query": dict(req.query_params), "body": body, "status": resp.status_code})
            return resp

        return wrapped


router = APIRouter(prefix=PREFIX, route_class=MockRoute)
A = "/api/v3/accounts/{account_id}"


def ctx(account_id: str, key: str = Header("", alias="X-Payhawk-ApiKey")) -> str:
    if not key:
        raise PayhawkError(401, "Unauthorized")
    if account_id != store.ref["account"]["id"]:
        raise PayhawkError(404, "NotFound", "Account not found")
    return account_id


def out(req: Request, data: Any, status: int = 200) -> JSONResponse:
    base = f"{str(req.base_url).rstrip('/')}{PREFIX}{A.replace('{account_id}', store.ref['account']['id'])}"
    return JSONResponse(json.loads(json.dumps(data).replace(FIXTURE_BASE, base)), status)


def page(items: list, skip: int, take: int | None) -> dict:
    take = 1000 if take is None else take
    if skip < 0 or not 1 <= take <= MAX_TAKE:
        raise PayhawkError(400, "BadRequest", f"$take must be 1..{MAX_TAKE}, $skip >= 0")
    return {"items": items[skip: skip + take], "total": len(items)}


# ---- $filter / $orderBy ----------------------------------------------------------
def _get(obj: Any, path: str) -> Any:
    for part in path.split("."):
        obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


def _norm(v: Any) -> Any:
    # Payhawk date filters compare the date part only (ignore time of day).
    return v[:10] if isinstance(v, str) and len(v) >= 10 and v[4:5] == "-" and v[7:8] == "-" else v


def _op(val: Any, op: str, arg: Any) -> bool:
    if op == "$empty":
        return (val in (None, "", [])) == arg
    if val is None:
        return op == "$notEqual"
    v, a = _norm(val), _norm(arg)
    return {"$equal": lambda: v == a, "$notEqual": lambda: v != a,
            "$contains": lambda: str(a).lower() in str(v).lower(),
            "$in": lambda: v in arg, "$lessThan": lambda: v < a, "$greaterThan": lambda: v > a}[op]()


def matches(item: dict, flt: dict) -> bool:
    for k, cond in flt.items():
        if k == "$and":
            ok = all(matches(item, f) for f in cond)
        elif k == "$or":
            ok = any(matches(item, f) for f in cond)
        else:
            try:
                ok = all(_op(_get(item, k), op, arg) for op, arg in cond.items())
            except (KeyError, TypeError):
                raise PayhawkError(400, "BadRequest", f"Unsupported filter on {k}")
        if not ok:
            return False
    return True


def parse_json_param(raw: str | None, name: str) -> Any:
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        raise PayhawkError(400, "BadRequest", f"{name} must be URL-encoded JSON")


def list_response(items, filter_, skip, take, order=None):
    flt = parse_json_param(filter_, "$filter")
    if flt:
        items = [i for i in items if matches(i, flt)]
    if order:
        o = parse_json_param(order, "$orderBy")
        if not isinstance(o, list) or len(o) != 1 or len(o[0]) != 1:
            raise PayhawkError(400, "BadRequest", "$orderBy must be an array with one element")
        (field, direction), = o[0].items()
        if field not in ("id", "createdAt", "settledAt") or direction not in ("asc", "desc"):
            raise PayhawkError(400, "BadRequest", "Can only order by id, createdAt, settledAt")
        key = (lambda i: int(i["id"])) if field == "id" else (lambda i: i.get(field) or "")
        items = sorted(items, key=key, reverse=direction == "desc")
    return page(items, skip, take)


# ---- request bodies (extra=forbid mirrors the spec's additionalProperties:false) ----
class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Ref(Strict):
    id: str


class ExpensePatch(Strict):
    supplier: Ref | None = None
    category: Ref | None = None
    taxRate: Ref | None = None
    note: str | None = None
    documentNumber: str | None = None
    documentDate: str | None = None
    amortization: dict | None = None
    externalLinks: list[dict] | None = None


class StatusPatch(Strict):
    status: Literal["draft", "ready"]


class NewComment(Strict):
    targetType: Literal["expense", "purchase_order"]
    targetId: str
    payload: str


class NewSupplier(BaseModel):
    name: str


class CustomFieldPatch(Strict):
    value: str | None = None
    selectedValues: list | None = None


def touch(e: dict):
    e["modifiedAt"] = now()


# ---- endpoints -----------------------------------------------------------------
@router.get(A)
def get_account(req: Request, _=Depends(ctx)):
    return out(req, store.ref["account"])


@router.get(A + "/expenses")
def list_expenses(req: Request, _=Depends(ctx)):
    return _list(req, store.visible(), order=True)


@router.get(A + "/expenses/{expense_id}")
def get_expense(req: Request, expense_id: str, _=Depends(ctx)):
    return out(req, store.expense(expense_id))


@router.patch(A + "/expenses/{expense_id}")
def patch_expense(req: Request, expense_id: str, body: ExpensePatch, _=Depends(ctx)):
    e = store.expense(expense_id)
    if e["status"] != "draft":
        raise PayhawkError(400, "BadRequest", "Only draft expenses can be updated")
    if body.supplier:
        e["supplier"] = next((s for s in store.ref["suppliers"] if s["id"] == body.supplier.id), None) \
            or _missing("supplier")
    if body.category:
        e["category"] = next((c for c in store.ref["categories"] if c["id"] == body.category.id), None) \
            or _missing("category")
    doc = e.setdefault("document", {"files": []})
    for field in ("documentNumber", "documentDate"):
        if field in body.model_fields_set:
            doc[field] = getattr(body, field)
    if "note" in body.model_fields_set:
        e["note"] = body.note
    if body.amortization is not None:
        e["amortization"] = body.amortization
    touch(e)
    return out(req, e)


def _missing(what: str):
    raise PayhawkError(400, "BadRequest", f"Unknown {what} id")


@router.post(A + "/expenses/{expense_id}/status")
def set_status(req: Request, expense_id: str, body: StatusPatch, _=Depends(ctx)):
    e = store.expense(expense_id)
    if e["status"] not in ("draft", "ready"):
        raise PayhawkError(400, "BadRequest", f"Cannot change status of a {e['status']} expense")
    e["status"] = body.status
    if body.status == "ready":
        e["reviewedAt"], e["reviewedBy"] = now(), {"id": "api-key", "email": ""}
    else:
        e.pop("reviewedAt", None), e.pop("reviewedBy", None)
    touch(e)
    return out(req, e)


@router.get(A + "/expenses/{expense_id}/files")
def list_files(req: Request, expense_id: str, _=Depends(ctx)):
    files = store.expense(expense_id)["document"]["files"]
    return out(req, {"items": files, "total": len(files)})


@router.get(A + "/expenses/{expense_id}/files/{file_id}")
def get_file(req: Request, expense_id: str, file_id: str, _=Depends(ctx)):
    f = next((f for f in store.expense(expense_id)["document"]["files"] if f["id"] == file_id), None)
    if not f:
        raise PayhawkError(404, "NotFound", "Expense file not found")
    return out(req, f)


@router.get(A + "/expenses/{expense_id}/files/{file_id}/content")
def file_bytes(expense_id: str, file_id: str, _=Depends(ctx)):
    if not any(f["id"] == file_id for f in store.expense(expense_id)["document"]["files"]):
        raise PayhawkError(404, "NotFound", "Expense file not found")
    return FileResponse(FIXTURES / "receipts" / f"{file_id}.png", media_type="image/png")


@router.get(A + "/expenses/{expense_id}/payments")
def payments(req: Request, expense_id: str, _=Depends(ctx)):
    store.expense(expense_id)
    items = store.ref["payments"].get(expense_id, [])
    return out(req, {"items": items, "total": len(items)})


@router.get(A + "/expenses/{expense_id}/workflow")
def workflow(req: Request, expense_id: str, _=Depends(ctx)):
    e = store.expense(expense_id)
    w = {"approvalStatus": e["approvalProcess"]["status"]}
    for k in ("reviewedAt", "reviewedBy"):
        if k in e:
            w[k] = e[k]
    return out(req, w)


@router.patch(A + "/expenses/{expense_id}/custom-fields/{cf_id}")
def patch_custom_field(expense_id: str, cf_id: str, body: CustomFieldPatch, _=Depends(ctx)):
    e = store.expense(expense_id)
    cf = next((c for c in store.ref["custom_fields"] if c["id"] == cf_id), None)
    if not cf:
        raise PayhawkError(404, "NotFound", "Custom field not found")
    cfs = [c for c in e["reconciliation"].setdefault("customFields", []) if c["id"] != cf_id]
    if body.value is not None or body.selectedValues:
        cfs.append({"id": cf_id, "label": cf["label"], "value": body.value or ""})
    e["reconciliation"]["customFields"] = cfs
    touch(e)
    return Response(status_code=204)


@router.post(A + "/comments")
def create_comment(req: Request, body: NewComment, _=Depends(ctx)):
    if body.targetType == "expense":
        store.expense(body.targetId)
    c = {**body.model_dump(), "id": store.next_id(), "createdAt": now(), "createdBy": {"id": "api-key"}}
    store.comments.append(c)
    return out(req, c)


@router.get(A + "/comments/{target_type}/{target_id}")
def list_comments(req: Request, target_type: str, target_id: str, _=Depends(ctx)):
    items = [c for c in store.comments if c["targetType"] == target_type and c["targetId"] == target_id]
    return out(req, {"items": items, "total": len(items)})


def _int(q, name: str, default):
    try:
        return int(q[name]) if name in q else default
    except ValueError:
        raise PayhawkError(400, "BadRequest", f"{name} must be an integer")


def _list(req: Request, items: list, order: bool = False):
    q = req.query_params
    return out(req, list_response(items, q.get("$filter"), _int(q, "$skip", 0), _int(q, "$take", None),
                                  q.get("$orderBy") if order else None))


def _simple_list(req: Request, key: str):
    return _list(req, store.ref[key])


@router.get(A + "/expense-categories")
def categories(req: Request, _=Depends(ctx)):
    return _simple_list(req, "categories")


@router.get(A + "/suppliers")
def suppliers(req: Request, _=Depends(ctx)):
    return _simple_list(req, "suppliers")


@router.post(A + "/suppliers")
def create_supplier(req: Request, body: NewSupplier, _=Depends(ctx)):
    if not body.name.strip():
        raise PayhawkError(400, "BadRequest", "name is required")
    s = {"id": str(301 + len(store.ref["suppliers"])), "name": body.name, "status": "active"}
    store.ref["suppliers"].append(s)
    return out(req, s, 201)


@router.get(A + "/users")
def users(req: Request, _=Depends(ctx)):
    return _simple_list(req, "users")


@router.get(A + "/custom-fields")
def custom_fields(req: Request, _=Depends(ctx)):
    return _simple_list(req, "custom_fields")


# ---- mock-only simulation controls ------------------------------------------------
@router.get("/__log")
def get_log():
    return store.log


@router.post("/__reset")
def reset(released: int | None = None):
    store.reset(released)
    return {"released": store.released, "total": len(store.expenses)}


@router.post("/__arrive")
def arrive(n: int = 1):
    """Release the next n expenses as if they just arrived (fresh createdAt/modifiedAt)."""
    new = store.expenses[store.released: store.released + n]
    store.released += len(new)
    for e in new:
        e["createdAt"] = e["modifiedAt"] = now()
    return {"arrived": [e["id"] for e in new], "released": store.released}


@router.post("/__edit/{expense_id}")
def edit(expense_id: str, changes: dict[str, Any] | None = None):
    """Simulate an employee edit: apply dotted-path changes (bypasses the draft rule) and bump modifiedAt."""
    e = store.expense(expense_id)
    for path, value in (changes or {}).items():
        *parents, leaf = path.split(".")
        node = e
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = copy.deepcopy(value)
    touch(e)
    return e
