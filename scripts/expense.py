"""Print one Payhawk expense as raw JSON: uv run python -m scripts.expense <id>"""
import json, sys
from app.payhawk.client import PayhawkClient

print(json.dumps(PayhawkClient().expense(sys.argv[1]), indent=2))
