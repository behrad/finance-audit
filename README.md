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

Secrets live in the committed, **encrypted** `.env` ([dotenvx](https://dotenvx.com)); the private key is in `.env.keys` (gitignored, never commit, never bake into an image).
Get `.env.keys` from a teammate / the password manager, then:

```bash
npx @dotenvx/dotenvx run -- uv run uvicorn app.web:app --port 8080   # live Payhawk (badge shows LIVE)
npx @dotenvx/dotenvx set NAME value                                  # add/change a secret (re-encrypts .env)
```

Start / stop (needs `.env.keys`; `PORT=8081` if 8080 is busy):

```bash
./scripts/start.sh            # local, background; logs in data/service.log
./scripts/stop.sh
./scripts/start.sh --docker   # docker compose (builds the image, passes DOTENV_PRIVATE_KEY from .env.keys)
./scripts/stop.sh  --docker
```

Raw JSON of one expense (mock unless run under dotenvx):

```bash
npx @dotenvx/dotenvx run -q -- uv run python -m scripts.expense 214   # live
```

The **Settings** page in the UI shows mock vs live, Payhawk host/account id, last poll duration, poll interval
(`POLL_INTERVAL_SECONDS`, 0 = manual), LLM provider/model/mode, and write-back dry-run, plus a "Test Payhawk connection" button.

Docker / Google Cloud: ship the encrypted `.env` in the image, never `.env.keys`. Provide the key at runtime as the single env var
`DOTENV_PRIVATE_KEY` (Cloud Run: Secret Manager secret exposed as env var; compose: `DOTENV_PRIVATE_KEY=$(...)` from your shell).
Without it the app falls back to the mock. Variable names are `PAYHAWK_API_KEY` and `PAYHAWK_BASE_URL` (see `.env.example`).
Write-back is **live by default**. `WRITEBACK_DRY_RUN=1` (or the Settings toggle) makes it log-only. Comments you post land on
`/comments/expense/{id}` in Payhawk, not on the expense JSON; the "Fetch expense from Payhawk" panel shows both.

## Rules: editing, per customer, renaming codes

A customer's rules live in one file, `skills/<tenant>/SKILL.md`. If there is no folder for a tenant,
`skills/default/` is used. A file has two parts:

- **Frontmatter** (between the `---` lines): what the code runs. `model`, `flags` (known codes),
  `checks` (deterministic checks from `app/pipeline/checks.py`, run in that order, with their params),
  `categories`, `fallback_category`.
- **Body**: the customer's rules in plain English, exactly as they wrote them. It is injected into our fixed
  system prompt (`SYSTEM_PROMPT` in `app/pipeline/llm.py`) as `<user_rules>…</user_rules>`. Our own
  instructions go in `SYSTEM_PROMPT`, never in the body.

How a rule gets enforced:
1. **The code already checks it**: list the check under `checks:`. The AI is told not to redo it.
2. **No check for it yet**: write it in the body only. The AI judges it and returns a flag. It uses a known
   code if one fits, otherwise a new snake_case code (or the one the rule names). New codes are accepted.
3. **It must be exact** (money, dates, numbers): add a function to `checks.py`, add its default code to
   `FLAGS`, list it under `checks:` and add a test in `tests/test_checks.py`.

### Changing things without breaking anything
1. **Rename a code**: put `flag: <new_code>` on the check, e.g. `fx_deviation: {..., flag: fx_difference}`.
   Don't edit the code in `checks.py`. The renamed code is added to the known flags automatically, so `flags:`
   can't fall out of sync. Also use the same code in the body text if the rule mentions it.
2. **One source of truth**: each check's default code is listed only in `FLAGS` (`checks.py`). Check
   functions read it from there.
3. **Typos are rejected**: a check name under `checks:` that the code doesn't have (e.g. `number_matchez`)
   is an error when you save or load, and the error lists the valid names. Nothing is skipped silently.
4. **Rules with no check still work**: see point 2 above.
5. **Edited rules trigger a re-review**: the rules' version is a hash of the file. A changed file makes every
   expense pending again (`jobs.pending`), so old and new results are never mixed. Reviews saved earlier keep
   their old codes until they are reviewed again.

### Where to edit
- **Rules page** (dashboard): saves an override to `data/skills/default/SKILL.md` (gitignored), which wins
  over the repo file. "Reset" deletes the override. Invalid rules are refused with the error.
- **In the repo**: edit `skills/<tenant>/SKILL.md` and commit. That's for defaults and for new customers.
- **New customer**: copy `skills/default/` to `skills/<tenant>/`, then edit the body and params.
  Limitation: the web app and jobs only use `default` for now (`SkillRegistry().get()` in `app/web.py`,
  `analyze(tenant="default")` in `app/jobs.py`). Real multi-tenant needs that tenant passed through.

### After any change
- Run `uv run pytest -q`. `test_pipeline_fixtures.py` compares flag codes against `fixtures/expected.json`.
  If you renamed a code on purpose, update that file (and `expect=` in `scripts/make_mock_receipts.py`).
- Renaming a code changes what lands in the CSV and the dashboard (`flags`, `category_for_attention`). Payhawk
  comments only carry the description text. Tell whoever filters on the old name.
- Not covered by the code yet: the duplicate "original" marking is only shown in the dashboard / CSV
  (`duplicate_role`), not written back to Payhawk.

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

## Offline mode

Settings -> Data source switches between LIVE (Payhawk API) and OFFLINE (local files, no network, write-backs are no-ops).
`PUT /api/settings/mode {"mode": "offline"|"live"}`; default from `DATA_MODE` (live), folder from `OFFLINE_DIR` (`data/offline/expenses`).
Layout: `<id>.json` (raw `GET /expenses/{id}`) + `<id>/<fileId>.pdf`. `data/` is git-ignored (real company data).
Snapshot live expenses: `PYTHONPATH=. npx @dotenvx/dotenvx run -q -- uv run python -m scripts.offline_snapshot 224 223`.
