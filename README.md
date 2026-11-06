# Receipt Auditor

Polls Payhawk expenses, reads each receipt with Gemini, checks it against the expense and the tenant's rules
(`skills/<tenant>/SKILL.md`), shows the result in a dashboard / CSV, and writes fixes and decisions back to Payhawk.
Design: `docs/SPEC.md`. Decisions: `docs/DECISIONS.md`.

**Default = mock mode, zero keys.** A mock Payhawk API runs inside the app (`/mock-payhawk`) and Gemini answers are replayed from `fixtures/llm/`.

## Run locally

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run uvicorn app.web:app --port 8080     # port 8080 matters: the client's default mock URL is localhost:8080
```

Open http://localhost:8080 and press **Poll now**. You get 14 reviews (4 clean, 10 needs attention); expense 1002 is auto-approved
(under $10), 1009 / 1010 get their supplier / category fixed in the mock. Use Approve / Reject in the drawer, then **Apply actions**.

Useful URLs: `/api/reviews`, `/api/export.csv`, `/api/runs` (write-back log), `/mock-payhawk/__log`, `POST /mock-payhawk/__reset`.
CLI jobs: `uv run python -m app.jobs poll` (needs the server running for the mock).

State is in memory: restart the app and the mock together to start clean.

### Docker

```bash
docker build -t receipt-auditor .
docker run --rm -p 8080:8080 receipt-auditor
```
Verified: builds, `/healthz` ok, poll reviews 14 expenses inside the container.
`docker-compose.yml` also starts a Firestore emulator, but **the app does not use it yet** (state is in memory), so prefer plain `docker run`.

### Live Gemini (optional)

```bash
LLM_MODE=live GOOGLE_API_KEY=... uv run uvicorn app.web:app --port 8080
```
Not exercised yet. The model name `gemini-3.8-flash` in `skills/default/SKILL.md` is unconfirmed (P13).

### Live Payhawk (optional)

`PAYHAWK_BASE_URL=https://api.payhawk.com/api/v3/accounts/<id> PAYHAWK_API_KEY=...` switches the client (badge shows LIVE).
Not tested against a real account. Write-back has a `dry_run` switch (`app/writeback.py` `Switches`) that should be on for the first live run;
it currently defaults to **off** and is not yet configurable from env or UI.

## Test

```bash
uv run pytest -q          # 14 tests, no network, no keys
```
- `test_mock_contract.py`: mock responses validated against Payhawk's OpenAPI spec (`contracts/payhawk-v3.json`)
- `test_pipeline_fixtures.py`: all 14 fixtures reproduce `fixtures/expected.json`
- `test_checks.py`: boundaries (exactly 2%, month edges, duplicates)
- `test_jobs.py`, `test_web.py`: write-back, idempotency, API

Regenerate fixtures: `uv run python scripts/make_mock_receipts.py`.

## Is it ready to deploy to Google Cloud?

**The container is; the system is not.** It would start on Cloud Run, but as a demo only.

| Area | State |
|---|---|
| Image | Builds and runs; listens on `$PORT` (Cloud Run compatible). Runs as root (should add a non-root user). |
| Persistence | **Missing.** State is in memory, lost on restart, and breaks with more than one instance. Firestore (P3) is not implemented. |
| Jobs | `python -m app.jobs` exists, but it talks to a Payhawk URL, and the mock only lives inside the web process. Cloud Run Jobs + Scheduler not set up (R11). |
| Auth | **None.** Dashboard and `/api/runs/*` are open. IAP (P4) not configured; do not expose publicly. |
| Secrets / settings | No `/settings` page, no Secret Manager. Keys only via env vars. |
| Google Sheet | Not implemented (CSV only). |
| CI/CD | No `.github/workflows`, no `infra/bootstrap.sh`. |
| Live Payhawk / Gemini | Untested (no account / key). |

To deploy a demo today: `gcloud run deploy receipt-auditor --source . --no-allow-unauthenticated --max-instances 1` (single instance because of the in-memory state; authenticated access only, e.g. via the `gcloud run services proxy`).
Not yet done or verified by me: this command was not run.

Next for production: Firestore store, IAP, jobs + Scheduler, Secret Manager + settings page, Sheets sync, GitHub Actions, then a live dry-run against a real Payhawk account.
