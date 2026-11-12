"""Snapshot live expenses for offline mode: uv run python -m scripts.offline_snapshot <id> [<id>...]
Needs live creds (npx @dotenvx/dotenvx run -- ...). Writes data/offline/expenses/<id>.json + <id>/<fileId>.<ext>."""
import json, sys
from pathlib import Path
from app.payhawk.client import PayhawkClient
from app.payhawk.offline import OFFLINE_DIR
from app.rows import row

REQ = {"owner": ["firstName", "lastName"], "reconciliation": ["baseTotalAmount", "baseCurrency", "totalAmount", "currency"],
       "document": ["documentDate", "files"]}
EXT = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}

c, root = PayhawkClient(), Path(OFFLINE_DIR)
for eid in sys.argv[1:]:
    e = c.expense(eid)
    for k, subs in REQ.items():
        for s in subs:
            if s not in (e.get(k) or {}):
                print(f"warn {eid}: missing {k}.{s}", file=sys.stderr)
    for k in ("supplier", "category", "note"):  # optional in row(), but worth knowing
        if not e.get(k):
            print(f"warn {eid}: empty {k}", file=sys.stderr)
    (root / eid).mkdir(parents=True, exist_ok=True)
    for f in e["document"]["files"]:
        (root / eid / (f["fileId"] + EXT.get(f["contentType"], ""))).write_bytes(c.download(f))
    (root / f"{eid}.json").write_text(json.dumps(e, indent=2))
    row(e, {"receipt": {"total": 0, "currency": ""}, "duplicate_of": None})  # KeyError here = unusable snapshot
    print("saved", eid)
