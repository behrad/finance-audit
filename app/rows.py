"""One review + its Payhawk expense -> one sheet/dashboard/CSV row (SPEC §6 columns)."""
COLUMNS = ["expense_id", "employee", "supplier", "category", "note", "document_date", "service_start", "service_end",
           "document_number", "form_amount", "form_currency", "total_amount", "total_currency", "receipt_total",
           "receipt_currency", "receipt_link", "status", "category_for_attention", "description", "action", "comment",
           "changes_applied", "duplicate_role", "duplicate_of", "action_source", "processed_at", "skill_version", "model"]


def row(e: dict, r: dict, originals: set = frozenset()) -> dict:
    rec, doc, am, o = e["reconciliation"], e["document"], e.get("amortization") or {}, e["owner"]
    return {
        "expense_id": e["id"], "employee": f"{o['firstName']} {o['lastName']}",
        "supplier": (e.get("supplier") or {}).get("name", ""), "category": (e.get("category") or {}).get("name", ""),
        "note": e.get("note", ""), "document_date": doc["documentDate"][:10],
        "service_start": (am.get("startDate") or doc.get("serviceDate") or "")[:10], "service_end": am.get("endDate", "")[:10],
        "document_number": doc.get("documentNumber", ""),
        "form_amount": rec["baseTotalAmount"], "form_currency": rec["baseCurrency"],
        "total_amount": rec["totalAmount"], "total_currency": rec["currency"],
        "receipt_total": r["receipt"]["total"], "receipt_currency": r["receipt"]["currency"],
        "receipt_link": f"/api/receipts/{e['id']}/{doc['files'][0]['fileId']}",
        "duplicate_role": "duplicate" if r["duplicate_of"] else "original" if e["id"] in originals else "", "flags": r.get("flags", []), "flag_details": r.get("flag_details", []), **{
            k: r.get(k, "") for k in ("status", "category_for_attention", "description", "action", "comment", "changes_applied",
                                      "duplicate_of", "action_source", "processed_at", "skill_version", "model")},
    }
