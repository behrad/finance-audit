# Decisions log: Receipt Auditor

Single source of truth for what is locked. `docs/SPEC.md` describes the design built on these decisions.
Review each row. To change one, edit the **Answer** and set **Status** back to `OPEN`.

Status values:
- `LOCKED`: you answered it.
- `RESEARCH`: forced by what Payhawk, Google ADK or GCP actually support. It can only change if they change.
- `PROPOSED`: Claude's default. It applies unless you change it.
- `OPEN`: needs your answer or a real account.

Last updated: 2026-11-08.

---

## A. Locked by you

| ID | Question | Answer | Status |
|---|---|---|---|
| D1 | Should the service write back to Payhawk? | **Full**: auto-fix, auto-approve under $10, and process the action column from the sheet and dashboard. Implemented within what the API allows (see R1, R2, D9). | LOCKED |
| D2 | Where does finance mainly work? | **Both, equally**: the Google Sheet and our own dashboard (like `standalone.html`), backed by the same data. | LOCKED |
| D3 | Which FX rate counts for the 2% check? | **Payhawk's rate on the expense** (`reconciliation.fxRate` / `baseTotalAmount`). No external FX API. The card-charged rate printed on the receipt is ignored. | LOCKED |
| D4 | How multi-tenant is v1? | **Tenant-ready, one tenant live.** Everything is keyed by `tenant_id`. Per-tenant IAM and Agent Runtime come later. | LOCKED |
| D5 | How are fixes applied, given PATCH only works on drafts? | **Auto-PATCH while `draft`.** Once reviewed, the fix is shown as a suggestion only. | LOCKED |
| D6 | "Service period within current month", relative to what? | **The month of the check run.** The end date may be at most 30 days after the run date. | LOCKED |
| D7 | Is "supplier = employee name" literal? | **Yes.** Flag it and auto-fix it to the claimant's name. | LOCKED |
| D8 | Stack | Python + FastAPI + Google ADK. Docker. Runs locally and on Cloud Run. **Two LLM providers, same behaviour: Gemini and OpenAI (ChatGPT), both through ADK.** Locally you use your Gemini key and your colleague uses their ChatGPT key. The Cloud Run deployment will probably use the ChatGPT key. The provider is picked by whichever key and model is configured, and the pipeline, skill and results must not depend on it. We enter the LLM keys ourselves. | LOCKED |
| D9 | What do approve and reject do, since there's no approve endpoint? You asked whether the MCP could be used with a single login. | **MCP isn't possible today** (R3). So: **clean / approve → mark `ready`** (reviewed). **Needs attention / reject → stays `draft`, plus a Payhawk comment with our flags and descriptions.** Finance approves inside Payhawk. | LOCKED |
| D10 | Does the prototype need Payhawk access? | **No.** Mock mode is the default, with the same API paths, fields, raw data and receipts as Payhawk. Adding an API key later switches it to live with no code change. | LOCKED |
| D11 | Pluggability | The rules step must be modular and injectable per customer: their own SKILL.md, their own model, and later their own policies and permissions. | LOCKED |
| D12 | Configuration UI | The Payhawk API key and account ID, and the LLM key, can be set from the UI. | LOCKED |

## B. Forced by research (APIs as they are today)

| ID | Fact | Consequence | Source |
|---|---|---|---|
| R1 | Payhawk REST v3 has **no approve or reject endpoint**. | Approve maps to `POST /expenses/{id}/status {"status":"ready"}`. Reject maps to a comment. | `docs/research/payhawk-api.md` §6 |
| R2 | `PATCH /expenses/{id}` works **only while `draft`**. Supplier and category are set by **ID**, not by name. | Fixes go in before marking `ready`. We look up or create IDs via `/suppliers` and `/expense-categories`. | §6 |
| R3 | The Payhawk MCP only allows sign-in from allowlisted assistants (Claude, ChatGPT, Cursor and others; **not Gemini or ADK**). A session lasts at most 30 days, and every write waits for a person to confirm. | We can't use it for an unattended service. Impersonating an allowlisted client is ruled out. We keep a `PayhawkClient` interface so an MCP adapter can be added if Payhawk allowlists us. | payhawk.com/help/connecting-payhawk-mcp, payhawk-mcp-overview |
| R4 | The `$filter` on dates **ignores the time of day**. There is no sort by `modifiedAt`. Paging is offset-based. | Each poll re-fetches a two-day window and deduplicates by `(id, modifiedAt)`. | §3 |
| R5 | Expense shape: `status` = draft/ready/archived/deleted. Approval state is in `approvalProcess.status`. Amounts are in `reconciliation.*`. `owner` has `firstName`/`lastName` (no `name`). There is no `serviceEndDate`. | The prototype's field names are corrected. The mock uses the real shape. | §4 |
| R6 | 15 requests per second per key. Errors look like `{code, message}`. | The client throttles and retries on 429 using `Retry-After`. | §8 |
| R7 | Webhooks exist (`expense.created`, `expense.document.uploaded`, and others), but there is no `expense.updated` event. | **Webhooks are not needed for now.** We poll only (we read `approvalProcess.status` on each poll instead of listening for `expense.approved`). | §7 |
| R8 | ADK `SkillToolset` lets the model decide whether to load a skill. | The tenant's SKILL.md becomes the rules agent's instruction instead, so the rules always apply. | `docs/research/google-adk.md` |
| R9 | ADK 2.x deprecates `SequentialAgent` in favour of `Workflow`. Pin the `google-adk` version. | The pipeline is a `Workflow`. | google-adk.md |
| R10 | Sheets API: 60 writes per minute per user, and the one service account is a single user. | Writes are batched once per poll. | `docs/research/gcp-infra.md` |
| R11 | On Cloud Run with min-instances 0, an in-process cron isn't reliable. | Cloud Scheduler starts Cloud Run Jobs, built from the same image. **You confirmed this is reliable**: scheduling is done on GCP, not in the app. | gcp-infra.md |

## C. Proposed defaults (Claude's choices; they apply unless you change them)

| ID | Default | Alternative |
|---|---|---|
| P1 | **Split the rule engine.** Arithmetic, date and equality checks run as Python, switched on and given parameters in the SKILL.md frontmatter. The LLM only judges meaning (does the note or category fit the receipt) and writes descriptions. | Let the LLM do everything (less exact on the 2% and $10 rules). |
| P2 | **Gemini reads the receipt image or PDF directly.** No separate OCR (Document AI) step. | Add Document AI. |
| P3 | **State in Firestore**, using the emulator locally. Keys in Secret Manager locally via `.env` / `secrets.local.json`. Receipt copies in GCS, in a local folder when running locally. | Cloud SQL. |
| P4 | Dashboard auth: **IAP** on Cloud Run. No login on localhost. | Basic auth. |
| P5 | **Not needed for now.** No CI/CD and no Workload Identity Federation. The developer builds the Docker image and pushes it straight to **Artifact Registry** with their own credentials. Cloud Run and the Jobs use that image. | GitHub Actions + WIF, or Cloud Build triggers, later. |
| P6 | **Duplicates.** Fingerprint = merchant + receipt number + total + currency + date, checked against the tenant's full history. The earliest `createdAt` is the original. | Only within the same poll. |
| P7 | **Multiple flags.** `category_for_attention` holds the main flag. All flags go in `description`, separated by `; `. | One row per flag. |
| P8 | **Sheet vs dashboard actions.** Both write the same `action` field, and the most recent change wins. The service never overwrites `action` in the sheet. | The sheet is authoritative. |
| P9 | **Comments.** A Payhawk comment is posted once per distinct verdict, so the same result isn't posted twice. | A comment on every run. |
| P10 | **Write-back switches** per tenant (patch, mark ready, comment, dry run). `dry_run` is **on until the first verified live run**. Mock mode always writes, since there's no risk. | No switches. |
| P11 | **Mock in the app.** The mock Payhawk is a router inside the app (`/mock-payhawk`), checked against Payhawk's OpenAPI spec by contract tests. Without a Gemini key, `LLM_MODE=replay` uses recorded responses, so the demo needs **no keys at all**. | A separate mock container; always require a Gemini key. |
| P12 | **CSV fallback.** With no Google Sheet configured, the dashboard and a CSV export with the same columns stand in for the sheet. | Require a sheet. |
| P13 | **Model** set explicitly per tenant in the skill frontmatter, with a Gemini name (`gemini-3.8-flash`, **not confirmed yet**, check the Vertex model list) and an OpenAI name (not chosen yet; must read images/PDFs and return structured output). See D8. | `gemini-3.5-flash-lite` (cheaper). |
| P14 | **Flag vocabulary:** `duplicate, wrong_exchange_rate, wrong_value, total_mismatch, note_mismatch, supplier_not_employee, category_mismatch, date_mismatch, service_period_out_of_range, number_mismatch, unreadable_receipt`. | Edit the list. |
| P15 | **Poll timing.** Every 15 minutes. Receipts are kept for 90 days. | |
| P16 | **Re-review on edit.** An expense is re-evaluated whenever its `modifiedAt` or file list changes. | Evaluate once only. |

## D. Open: needs your answer or a real Payhawk account

None of these block the mock-mode prototype. Q1–Q4 block live mode only.

| ID | Question | Default until answered |
|---|---|---|
| Q1 | Do you have a Payhawk account (Labs Sandbox or real) with a **Full-scope API key**? | Mock only |
| Q2 | Where is the end of the service period stored: `amortization.endDate` or a custom field? | `amortization.startDate`/`endDate`, falling back to `document.serviceDate` |
| Q3 | Is `file.url` pre-signed, or does it need the API key? Which file types appear (PNG, JPEG, PDF, HEIC)? | Send the API key; handle images and PDFs |
| Q4 | What is the real maximum `$take` (1000 or 10000)? Is the `$greaterThan` date boundary inclusive? | 1000, and a two-day overlap |
| Q5 | Categories: Payhawk's `/expense-categories` list, or the skill's own list? | Payhawk's list; the skill's list is only a mapping hint |
| Q6 | May the service **create** a Payhawk supplier named after the employee if none exists? | Yes, logged in `changes_applied` |
| Q7 | Is the $10 threshold measured in the account's base currency? | Yes (`baseTotalAmount`, with the currency set in the skill) |
| Q8 | How long should receipts be kept in GCS? | 90 days |
| Q9 | Should we ask support@payhawk.com to allowlist our OAuth client for the MCP? | Not yet asked |
| Q10 | GCP project, region and GitHub repo for deployment | You'll provide these later. The workflow is written with placeholders. |

## Sign-off

- [ ] A. Locked decisions confirmed
- [ ] C. Proposed defaults accepted, or edited
- [ ] D. Q5–Q8 answered, or defaults accepted
