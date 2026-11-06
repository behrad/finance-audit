# Handoff: Receipt Auditor (2026-11-05)

## Where we are
- Research, spec and decisions are done. **No application code has been written yet.**
- Only `scripts/make_mock_receipts.py` exists. It generates `fixtures/`:
  - 14 receipt PNGs
  - `expenses.json`, in the real Payhawk v3 shape
  - `expected.json`, the expected verdicts
- Everything is untracked in git. Only the initial commit exists.

## Read first, in this order
1. `docs/DECISIONS.md`: the source of truth. Sections A and B are locked. **C (proposed defaults) is pending the user's sign-off.** Ask the user about C before building on it, unless they say "proceed with defaults".
2. `docs/SPEC.md`: architecture, pipeline and skill seam (§5), sheet columns (§6), write-back (§7), mock mode (§10), milestones (§13).
3. `docs/research/*.md`: only when you need a specific Payhawk, ADK or GCP fact. Grep them; they are long.
4. Do not read `standalone.html` in full (it is 500 KB, mostly inline demo data). It is the visual reference for the dashboard. Grep it for layout or CSS.

## Next step: milestone 1 (skeleton + mock Payhawk)
Build in this order and run the tests after each step:
1. Set up the project: `pyproject.toml` (uv, Python 3.12, pin `google-adk`), `app/`, `tests/`, `Dockerfile`, `docker-compose.yml` (app + Firestore emulator).
2. Save `https://api.payhawk.com/api/v3/docs.json` as `contracts/payhawk-v3.json`.
3. Extend `scripts/make_mock_receipts.py` with categories, suppliers, users, payments and custom fields that match the expenses.
4. Write `app/payhawk/mock.py`, a FastAPI router at `/mock-payhawk`. It must have the same paths, fields, errors and business rules as the real API (SPEC §10), plus the `__log`, `__reset`, `__arrive` and `__edit` controls.
5. Write `app/payhawk/client.py`, a `PayhawkClient` that uses REST over HTTP and is pointed at the mock by default.
6. Write `tests/test_mock_contract.py`, which validates the mock's responses against the OpenAPI spec.

Then milestone 2 (pipeline: extract → facts → rules, with LLM replay mode), milestone 3 (dashboard + sheet/CSV) and milestone 4 (write-back against the mock, which makes the demo-able prototype). Live Payhawk and GCP come after that.

## Constraints to remember
- The whole flow must run with **zero keys** in mock mode (D10, P11).
- The per-tenant skill and model must be injectable, only via `SkillRegistry` / `build_rules_agent` (SPEC §5).
- Not confirmed yet: the model name `gemini-3.8-flash` (P13), and Q1–Q10 in DECISIONS.md.
- The user writes in Persian, but replies are in English (global CLAUDE.md).
