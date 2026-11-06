"""Generate mock receipts + Payhawk v3-shaped expenses + expected verdicts.

Usage: python3 scripts/make_mock_receipts.py   (writes fixtures/)
Each case trips exactly one rule from the 'Skill payhawk' ruleset, so the set
doubles as the regression suite for the rules engine.
"""
import json
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "fixtures"
ACCOUNT = "demo_account"
BASE = f"https://api.payhawk.com/api/v3/accounts/{ACCOUNT}"


CATEGORIES = ["Meals", "Travel", "Accommodation", "Office supplies", "Software", "Learning", "Equipment"]
CAT_ID = {n: str(201 + i) for i, n in enumerate(CATEGORIES)}
CF_AI_REVIEW = "401"


def user_id(first):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{first.lower()}.demo.example"))


def supplier_names():
    return sorted({c["form"].get("supplier", " ".join(c["emp"])) for c in CASES})


def font(size, bold=False):
    for p in (["/System/Library/Fonts/Supplemental/Arial Bold.ttf"] if bold else []) + [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# id, employee, merchant, receipt lines, receipt (total, ccy, no, date, period),
# form overrides, expected flags
CASES = [
    dict(id="1001", emp=("Anna", "de Vries"), merchant="Brasserie Noord", lines=[("Lunch menu x2", 42.80)],
         r=("USD", "R-1001", "2026-11-02", None), form={}, cat="Meals", note="Client lunch", expect=[]),
    dict(id="1002", emp=("Bram", "Jansen"), merchant="Bean Counter Coffee", lines=[("Flat white x2", 6.40)],
         r=("USD", "BC-77", "2026-11-03", None), form={}, cat="Meals", note="Coffee with candidate",
         expect=[], action="approve", source="auto_under_10"),
    dict(id="1003", emp=("Chen", "Li"), merchant="City Taxi Amsterdam", lines=[("Ride Centraal > Zuidas", 38.50)],
         r=("USD", "C-5521", "2026-11-03", None), form={}, cat="Travel", note="Taxi to client office", expect=[]),
    dict(id="1004", emp=("Chen", "Li"), merchant="City Taxi Amsterdam", lines=[("Ride Centraal > Zuidas", 38.50)],
         r=("USD", "C-5521", "2026-11-03", None), form={}, cat="Travel", note="Taxi to client office",
         expect=["duplicate"], dup_of="1003"),
    dict(id="1005", emp=("Daan", "Bakker"), merchant="Hotel Adlon Berlin", lines=[("Room, 1 night", 120.00)],
         r=("EUR", "A-9001", "2026-11-01", None), form={"total": 150.00, "fx": 1.25}, cat="Accommodation",
         note="Hotel Berlin conference", expect=["wrong_exchange_rate"]),
    dict(id="1006", emp=("Eva", "Smit"), merchant="Deutsche Bahn", lines=[("ICE Amsterdam-Berlin", 50.00)],
         r=("EUR", "Z-310", "2026-11-01", None), form={"total": 54.50, "fx": 1.09}, cat="Travel",
         note="Train to Berlin", expect=[]),
    dict(id="1007", emp=("Fatima", "Yilmaz"), merchant="Office Depot", lines=[("Printer paper", 24.20), ("Toner", 40.00)],
         r=("USD", "C-883", "2026-11-02", None), form={"total": 46.20}, cat="Office supplies",
         note="Office supplies", expect=["wrong_value"]),
    dict(id="1008", emp=("Gijs", "Visser"), merchant="Trattoria Roma", lines=[("Pasta", 22.90), ("Pizza", 19.00), ("Wine", 17.00)],
         r=("USD", "R-204", "2026-11-02", None), form={}, cat="Meals", note="Flight to Berlin",
         expect=["note_mismatch"]),
    dict(id="1009", emp=("Hana", "Kato"), merchant="Staples", lines=[("Notebooks x5", 25.00)],
         r=("USD", "H-66", "2026-11-03", None), form={"supplier": "Staples"}, cat="Office supplies",
         note="Notebooks for workshop", expect=["supplier_not_employee"], fix={"supplier": "Hana Kato"}),
    dict(id="1010", emp=("Ivo", "Mulder"), merchant="Uber", lines=[("Trip to Schiphol", 31.00)],
         r=("USD", "S-4410", "2026-11-03", None), form={}, cat="Software", note="Taxi to airport",
         expect=["category_mismatch"], fix={"category": "Travel"}),
    dict(id="1011", emp=("Jade", "Peters"), merchant="Albert Heijn", lines=[("Team snacks", 18.75)],
         r=("USD", "AH-18", "2026-11-04", None), form={"date": "2026-10-12"}, cat="Meals",
         note="Snacks for team meeting", expect=["date_mismatch"]),
    dict(id="1012", emp=("Sajad", "Ahmadi"), merchant="Qwiklabs Inc.", lines=[("Google Skills Monthly Subscription", 29.00)],
         r=("USD", "2961-1182", "2026-08-28", ("2026-08-28", "2026-09-28")), form={}, cat="Learning",
         note="Learning budget", expect=["service_period_out_of_range"]),
    dict(id="1013", emp=("Karin", "Bos"), merchant="Bol.com", lines=[("USB-C hub", 45.00)],
         r=("USD", "INV-88123", "2026-11-02", None), form={"number": "INV-88132"}, cat="Equipment",
         note="USB-C hub for laptop", expect=["number_mismatch"]),
    dict(id="1014", emp=("Lars", "de Jong"), merchant="Parking Q-Park", lines=[("Parking 4h", 19.50)],
         r=("USD", "QP-501", "2026-11-04", None), form={}, cat="Travel", note="Parking client visit",
         expect=["unreadable_receipt"], blur=True),
]


def draw_receipt(c, path):
    ccy, number, date, period = c["r"]
    total = round(sum(a for _, a in c["lines"]), 2)
    W, H = 900, 1100
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    y = 50
    d.text((60, y), c["merchant"], font=font(40, True), fill="black"); y += 70
    d.text((60, y), "Receipt", font=font(30, True), fill="black"); y += 55
    for k, v in [("Receipt number", number), ("Date paid", date),
                 ("Bill to", " ".join(c["emp"]))]:
        d.text((60, y), f"{k}   {v}", font=font(22), fill="black"); y += 34
    if period:
        d.text((60, y), f"Service period   {period[0]} - {period[1]}", font=font(22), fill="black"); y += 34
    y += 30
    d.text((60, y), f"{total:.2f} {ccy} paid on {date}", font=font(28, True), fill="black"); y += 60
    d.line((60, y, W - 60, y), fill="black", width=2); y += 15
    d.text((60, y), "Description", font=font(20), fill="gray"); d.text((W - 220, y), "Amount", font=font(20), fill="gray"); y += 40
    for desc, amt in c["lines"]:
        d.text((60, y), desc, font=font(22), fill="black"); d.text((W - 220, y), f"{amt:.2f} {ccy}", font=font(22), fill="black"); y += 36
    y += 20
    d.line((W - 420, y, W - 60, y), fill="black", width=1); y += 15
    for k in ("Subtotal", "Total", "Amount paid"):
        d.text((W - 420, y), k, font=font(22, k == "Amount paid"), fill="black")
        d.text((W - 220, y), f"{total:.2f} {ccy}", font=font(22, k == "Amount paid"), fill="black"); y += 34
    if c.get("blur"):
        img = img.filter(ImageFilter.GaussianBlur(9))
    img.save(path)
    return total


def expense(c, total):
    ccy, number, date, period = c["r"]
    f = c["form"]
    fx = f.get("fx", 1.0)
    form_total = f.get("total", round(total * fx, 2))
    first, last = c["emp"]
    owner = {"id": user_id(first), "email": f"{first.lower()}@demo.example", "firstName": first, "lastName": last}
    file_id = f"f-{c['id']}"
    doc = {"documentDate": f.get("date", date) + "T00:00:00Z", "documentNumber": f.get("number", number),
           "documentType": "receipt", "serviceDate": (period[0] if period else date) + "T00:00:00Z",
           "files": [{"id": file_id, "fileId": file_id, "url": f"{BASE}/expenses/{c['id']}/files/{file_id}/content",
                      "contentType": "image/png"}]}
    sname = f.get("supplier", f"{first} {last}")
    e = {"id": c["id"], "title": c["note"], "note": c["note"], "type": "reimbursable", "status": "draft",
         "approvalProcess": {"status": "pending", "submittedAt": f"{date}T09:00:00Z"},
         "isPaid": False, "createdAt": f"{date}T09:00:00Z", "modifiedAt": f"{date}T09:00:00Z",
         "createdBy": owner, "owner": owner,
         "supplier": {"id": str(301 + supplier_names().index(sname)), "name": sname},
         "category": {"id": CAT_ID[c["cat"]], "name": c["cat"], "fullName": c["cat"]},
         "reconciliation": {"totalAmount": total, "currency": ccy, "baseTotalAmount": form_total,
                            "baseCurrency": "USD", "fxRate": fx, "customFields": []},
         "lineItems": [], "externalLinks": [], "document": doc}
    if period:  # Payhawk has no serviceEndDate; amortization holds the period (to confirm on a real account)
        e["amortization"] = {"startDate": period[0] + "T00:00:00Z", "endDate": period[1] + "T00:00:00Z"}
    return e


def reference_data(expenses):
    """Account-level data the expenses point at (categories, suppliers, users, custom fields)."""
    users = {e["owner"]["id"]: {**e["owner"], "role": "user", "createdAt": "2026-01-01T00:00:00Z"} for e in expenses}
    return {
        "account": {"id": ACCOUNT, "name": "Demo BV", "uic": "NL000000000", "baseCurrency": "USD"},
        "categories": [{"id": CAT_ID[n], "name": n, "fullName": n, "createdAt": "2026-01-01T00:00:00Z"} for n in CATEGORIES],
        "suppliers": [{"id": str(301 + i), "name": n, "status": "active"} for i, n in enumerate(supplier_names())],
        "users": list(users.values()),
        "custom_fields": [{"id": CF_AI_REVIEW, "label": "AI review", "type": "free_text_input", "isRequired": False}],
        "payments": {},  # all fixture expenses are unpaid reimbursables
    }


SEMANTIC = {"note_mismatch": "The note '{note}' does not describe this receipt ({merchant}).",
            "category_mismatch": "Category {cat} does not fit this purchase ({merchant})."}


def llm_fixtures(c, total):
    """Recorded LLM answers for LLM_MODE=replay: what a correct extraction / rules call returns for this case."""
    ccy, number, date, period = c["r"]
    extraction = {"merchant": c["merchant"], "receipt_number": number, "date": date, "total": total, "currency": ccy,
                  "service_start": period[0] if period else "", "service_end": period[1] if period else "",
                  "items": [d for d, _ in c["lines"]], "legibility": 0.2 if c.get("blur") else 0.95}
    flags = [{"name": f, "description": SEMANTIC[f].format(**c)} for f in c["expect"] if f in SEMANTIC]
    return extraction, {"flags": flags, "category_fix": c.get("fix", {}).get("category", "")}


def main():
    (OUT / "receipts").mkdir(parents=True, exist_ok=True)
    expenses, expected = [], []
    for c in CASES:
        total = draw_receipt(c, OUT / "receipts" / f"f-{c['id']}.png")
        expenses.append(expense(c, total))
        for kind, data in zip(("extract", "rules"), llm_fixtures(c, total)):
            (OUT / "llm" / kind).mkdir(parents=True, exist_ok=True)
            (OUT / "llm" / kind / f"{'f-' if kind == 'extract' else ''}{c['id']}.json").write_text(json.dumps(data, indent=2))
        expected.append({"expense_id": c["id"], "status": "needs attention" if c["expect"] else "clean",
                         "flags": c["expect"], "duplicate_of": c.get("dup_of", ""),
                         "fixes": c.get("fix", {}), "action": c.get("action", ""), "action_source": c.get("source", "")})
    (OUT / "expenses.json").write_text(json.dumps({"items": expenses, "total": len(expenses)}, indent=2))
    (OUT / "payhawk.json").write_text(json.dumps(reference_data(expenses), indent=2))
    (OUT / "expected.json").write_text(json.dumps(expected, indent=2))
    assert len({e["id"] for e in expenses}) == len(expenses)
    print(f"wrote {len(expenses)} receipts to {OUT}")


if __name__ == "__main__":
    main()
