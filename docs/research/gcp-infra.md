# GCP infrastructure research: Payhawk -> Gemini (ADK) -> Google Sheets service

Researched 2026-11-05 from primary docs. Note: `cloud.google.com/run/docs/...` and other product docs now
301-redirect to `docs.cloud.google.com/...`, so the citations below use the new host. Items marked
**UNVERIFIED** were not confirmed on a primary page during this session (for example, pricing pages
came back truncated).

Service shape: FastAPI on Cloud Run (and local Docker). It polls Payhawk on a schedule, reviews expenses
with Gemini through ADK, upserts rows into a Sheet, then reads the finance-edited `action` column
(approve/reject) and pushes the decisions back to Payhawk. A small UI stores per-tenant keys.

---

## 1. Scheduling

| Option | Works with min-instances=0? | Notes |
|---|---|---|
| **Cloud Scheduler -> Cloud Run service (OIDC)** | Yes. The HTTP request wakes an instance, and the work runs inside the request. | Request timeout defaults to 5 min, max 60 min ([request-timeout](https://docs.cloud.google.com/run/docs/configuring/request-timeout)). The Scheduler `attemptDeadline` defaults to 3 min, range 15 s to 30 min ([Scheduler REST](https://docs.cloud.google.com/scheduler/docs/reference/rest/v1/projects.locations.jobs)). So a poll that runs longer than 30 min shows as a failed attempt in Scheduler. |
| **Cloud Scheduler -> Cloud Run Job** | Yes. Jobs use instance-based billing ([billing](https://docs.cloud.google.com/run/docs/configuring/billing-settings)). | Task timeout defaults to 10 min, max 168 h. `--max-retries` defaults to 3, range 0-10 ([create-jobs](https://docs.cloud.google.com/run/docs/create-jobs), [task-timeout](https://docs.cloud.google.com/run/docs/configuring/task-timeout)). Can reuse the same image with a different command. Scheduler calls the Admin API with an **OAuth** token, not OIDC ([jobs-on-schedule](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule)). |
| **In-process APScheduler** | **No.** | With request-based billing, CPU is throttled outside requests. Idle instances are removed within about 15 min, and with min=0 no instance may exist when a trigger is due. Even with `--no-cpu-throttling`, instances "can be shut down at any time" ([billing](https://docs.cloud.google.com/run/docs/configuring/billing-settings)). It also fires once per instance, so N instances means N duplicate polls. |

**Service + OIDC** ([using-scheduler](https://docs.cloud.google.com/run/docs/triggering/using-scheduler)):
```bash
gcloud run services add-iam-policy-binding SVC --region R \
  --member serviceAccount:scheduler@P.iam.gserviceaccount.com --role roles/run.invoker
gcloud scheduler jobs create http poll --location R --schedule "*/15 * * * *" \
  --http-method POST --uri https://SVC-URL/tasks/poll \
  --oidc-service-account-email scheduler@P.iam.gserviceaccount.com \
  --oidc-token-audience https://SVC-URL --attempt-deadline 1800s
```

**Job**:
```bash
gcloud run jobs deploy poll --image IMG --region R --command python --args -m,app.poll \
  --service-account app@P.iam.gserviceaccount.com --task-timeout 30m --max-retries 1
gcloud scheduler jobs create http poll-job --location R --schedule "*/15 * * * *" \
  --uri "https://run.googleapis.com/v2/projects/P/locations/R/jobs/poll:run" \
  --http-method POST --oauth-service-account-email scheduler@P.iam.gserviceaccount.com
```
The scheduler SA needs `roles/run.invoker` on the job ([jobs-on-schedule](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule)). The `--command/--args` flag syntax is **UNVERIFIED**: the create-jobs page does not show it, so check the `gcloud run jobs deploy` reference.

**Recommendation: Cloud Scheduler -> Cloud Run Job**, sharing one image with the service. Reasons:
1. It sidesteps IAP. Section 8 puts IAP on the service. Scheduler calls through IAP need an IAP-specific audience, which is the OAuth client ID ([IAP programmatic auth](https://docs.cloud.google.com/iap/docs/authentication-howto)). Whether Scheduler's OIDC token works against direct IAP on Cloud Run is **UNVERIFIED**.
2. It allows long runs and retries.

If you skip IAP, Scheduler -> service `/tasks/poll` with OIDC is equally fine and takes one less deploy. One run polls every tenant in a loop. Use a per-tenant poll cursor (section 4) so runs are idempotent.

**Local dev:** `docker run --env-file .env IMG python -m app.poll`, or `curl -X POST localhost:8080/tasks/poll`. For a repeating trigger: `while true; do curl -X POST localhost:8080/tasks/poll; sleep 900; done`. Don't add APScheduler just for local use.

---

## 2. Google Sheets writes

**Library:** **gspread** for value reads and writes. For formatting and validation, send raw `spreadsheets.batchUpdate` bodies. gspread exposes a raw `Spreadsheet.batch_update(body)` (this method name is **UNVERIFIED** this session; it is from the gspread API docs, not Google's). google-api-python-client works the same way but is more verbose. gspread's docs only show key-file auth ([gspread oauth2](https://docs.gspread.org/en/latest/oauth2.html)). Passing ADC creds to `gspread.authorize(creds)` is the standard pattern but is **UNVERIFIED** on that page.

```python
import google.auth, gspread
creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
gc = gspread.authorize(creds)   # on Cloud Run, ADC = the service identity via the metadata server
```

**Auth choice:**
- **Recommended:** use the Cloud Run service account and share the sheet with its email. Google: "treat the service account's email address as a user account in the document's share settings". IAM roles do *not* grant Workspace access ([create-credentials](https://developers.google.com/workspace/guides/create-credentials)). Use a user-managed SA, not the Compute default, which may carry Editor ([service-identity](https://docs.cloud.google.com/run/docs/securing/service-identity)). Never set `GOOGLE_APPLICATION_CREDENTIALS` on Cloud Run (same page).
- **Domain-wide delegation:** only for acting as many users "without requiring individual user consent". It needs a Super Admin grant, so it is overkill here.
- **User OAuth:** needs consent and refresh-token storage. Use it only if customers refuse to share with an SA email. That could matter for multi-tenancy later: each tenant shares their sheet with your SA, so the tenant config stores `spreadsheet_id`.
- **Local:** use `gcloud auth application-default login --impersonate-service-account=app@P.iam.gserviceaccount.com`. This requires `roles/iam.serviceAccountTokenCreator` on that SA (**UNVERIFIED** this session). A downloaded SA key also works but is discouraged.

**Quotas** ([limits](https://developers.google.com/workspace/sheets/api/limits)): **300 read and 300 write requests per minute per project, and 60 read and 60 write per minute per user per project.** All tenants share one SA, which counts as **one user**, so 60 writes/min is the real ceiling. Each request is capped at 180 s, and the recommended payload is 2 MB max. On 429, use truncated exponential backoff (2^n s + up to 1 s jitter, max 32-64 s).

**Batching:** `spreadsheets.batchUpdate` is atomic ("if one request is unsuccessful, none of the other ... changes are written"). Use field masks to avoid overwriting ([batchupdate guide](https://developers.google.com/workspace/sheets/api/guides/batchupdate)). Do one `values.batchUpdate` per run, not one call per row.

**Upsert keyed by `expense_id`:**
1. `ws.col_values(ID_COL)` makes one read and builds `{expense_id: row_number}`. Re-read on every run, because finance may sort or insert rows.
2. Existing rows: one `ws.batch_update([{range: "A{r}:H{r}", values: [...]}, ...])`. Write only the columns the service owns. **Never write the `action` column**, or finance edits get lost.
3. New rows: one `ws.append_rows(rows, value_input_option="RAW")`.
4. Read-back: one `ws.get("A2:Z")` (or `batch_get` on the ID and action columns). Pair `action` with `expense_id`, and record in the state store what was pushed to Payhawk so you don't push twice.

**Dropdowns.** `DataValidationRule` has `condition`, `inputMessage`, `strict`, and `showCustomUi`. A dropdown renders when `showCustomUi=true` with a list condition ([cells ref](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/cells)). `ONE_OF_LIST` takes one `values` entry per item, and formulas are not allowed ([other ref](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/other)). The official sample shows only NUMBER_GREATER ([samples/data](https://developers.google.com/workspace/sheets/api/samples/data)), so this body is assembled from the reference:
```json
{"setDataValidation": {
  "range": {"sheetId": 0, "startRowIndex": 1, "startColumnIndex": 6, "endColumnIndex": 7},
  "rule": {"condition": {"type": "ONE_OF_LIST",
           "values": [{"userEnteredValue": "approve"}, {"userEnteredValue": "reject"}]},
           "strict": true, "showCustomUi": true}}}
```
Use the same shape for `status` with `clean` / `needs attention`. Omitting `endRowIndex` covers the whole column. Apply this **once at sheet setup**, not on every run.

**Row colouring:** use `addConditionalFormatRule` with `CUSTOM_FORMULA` ([samples/conditional-formatting](https://developers.google.com/workspace/sheets/api/samples/conditional-formatting)). Relative refs anchor to the range's top-left cell, so `$F2` follows the row:
```json
{"addConditionalFormatRule": {"index": 0, "rule": {
  "ranges": [{"sheetId": 0, "startRowIndex": 1}],
  "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
     "values": [{"userEnteredValue": "=$F2=\"needs attention\""}]},
   "format": {"backgroundColor": {"red": 1, "green": 0.9, "blue": 0.8}}}}}}
```
Add a second rule for `clean` in green. Send both validations and both rules in one `batchUpdate` at setup.

---

## 3. Per-tenant secrets entered through the UI

**Secret Manager** ([access-control](https://docs.cloud.google.com/secret-manager/docs/access-control), [quotas](https://docs.cloud.google.com/secret-manager/quotas)):
- Creating a secret needs `secretmanager.secrets.create`, which only **Admin** or **Editor** include. Adding a version to an existing secret needs **Secret Version Adder**. Reading needs **Secret Accessor**.
- IAM conditions on secret-name prefix are supported. Grant the app SA `roles/secretmanager.admin` conditioned on `resource.name.startsWith("projects/P_NUMBER/secrets/tenant-")`. The exact CEL form is **UNVERIFIED**; the page confirms prefix conditions exist.
- Limits: 64 KiB per version, 90,000 access requests/min, and 600 write requests/min per project.
- Pricing (about $0.06 per active version per month and $0.03 per 10k accesses, with a small free tier) is **UNVERIFIED** because the pricing page came back truncated. Disable or destroy old versions after rotation so they stop billing.

```python
from google.cloud import secretmanager
from google.api_core.exceptions import AlreadyExists
sm = secretmanager.SecretManagerServiceClient()
parent = f"projects/{P}"
sid = f"tenant-{tenant_id}-payhawk"
try:
    sm.create_secret(parent=parent, secret_id=sid, secret={"replication": {"automatic": {}}})
except AlreadyExists:
    pass
sm.add_secret_version(parent=f"{parent}/secrets/{sid}", payload={"data": key.encode()})
key = sm.access_secret_version(name=f"{parent}/secrets/{sid}/versions/latest").payload.data.decode()
```

**Firestore + KMS** means envelope encryption you write yourself: generate a DEK, encrypt it with KMS, store the ciphertext, and handle rotation. That is more code and more ways to get it wrong, with no gain at this scale.

**Recommendation: Secret Manager**, one secret per tenant per key, with secret IDs stored in the tenant config. The UI is write-only and never echoes keys back. **Local fallback:** the same two functions (`put_secret` and `get_secret`) backed by `.env` or a git-ignored `secrets.local.json`, selected by `SECRETS_BACKEND=local`.

---

## 4. State store (processed expenses, review results, poll cursor, tenant configs)

| Option | Local | Cloud Run (min=0) |
|---|---|---|
| SQLite on the container FS | Yes | **No.** The FS is in-memory and is lost when the instance stops ([container contract](https://docs.cloud.google.com/run/docs/container-contract)). On GCS FUSE there is no file locking and last write wins ([GCS volume mounts](https://docs.cloud.google.com/run/docs/configuring/services/cloud-storage-volume-mounts)). Unsafe. |
| Cloud SQL | Postgres in Docker | Works, but it is an always-on instance with a fixed monthly cost (**UNVERIFIED** price) plus connector setup. |
| **Firestore (Native)** | Emulator: `gcloud emulators firestore start` + `FIRESTORE_EMULATOR_HOST`, auto-detected by the Python client. It is in-memory and needs Java 21 from gcloud 528 ([emulator](https://docs.cloud.google.com/firestore/native/docs/emulator)). | Serverless, no connections to manage, scales to zero. The free tier (about 1 GiB stored, 50k reads/day, 20k writes/day) is **UNVERIFIED** because the pricing page came back truncated. |

**Recommendation: Firestore.** The same code runs against the emulator locally and the real database on Cloud Run. Layout:
`tenants/{tenant_id}` (config, sheet id, secret ids, `poll_cursor`),
`tenants/{tid}/expenses/{expense_id}` (status, review JSON, `sheet_synced_at`, `action_pushed`).
Advance the cursor in a transaction only after the sheet write succeeds. For persistent local data, an alternative is a real dev Firestore database in a separate project.

---

## 5. Receipt storage (GCS)

Layout: `gs://P-receipts/{tenant_id}/{expense_id}/{original_filename}`. Use one bucket with a tenant prefix, so IAM and lifecycle rules can target prefixes. Payhawk remains the system of record. **Keep a copy only for re-review and audit, and expire it with a lifecycle rule** ([lifecycle](https://docs.cloud.google.com/storage/docs/lifecycle)):
```json
{"lifecycle": {"rule": [{"action": {"type": "Delete"}, "condition": {"age": 90}}]}}
```
Deletes soft-delete for 7 days by default. Lifecycle changes take up to 24 h to apply. The retention period (90 days here) is a business and compliance decision, not a technical one. If Gemini can fetch the receipt directly from Payhawk on every review, skip GCS entirely.

---

## 6. CI/CD from GitHub

**GitHub Actions + Workload Identity Federation (recommended).** Use `google-github-actions/auth@v3` and `deploy-cloudrun@v3` (Node 24) ([auth](https://github.com/google-github-actions/auth), [deploy-cloudrun](https://github.com/google-github-actions/deploy-cloudrun), [GitHub OIDC for GCP](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-google-cloud-platform)). The job needs `id-token: write`. **Always** set an attribute condition on the provider. IAM changes take up to 5 min to propagate.

```bash
gcloud iam workload-identity-pools create github --location global
gcloud iam workload-identity-pools providers create-oidc repo --location global \
  --workload-identity-pool github --issuer-uri https://token.actions.githubusercontent.com \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --attribute-condition "assertion.repository == 'OWNER/temu-cart'"
gcloud iam service-accounts add-iam-policy-binding deployer@P.iam.gserviceaccount.com \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/PNUM/locations/global/workloadIdentityPools/github/attribute.repository/OWNER/temu-cart"
```

```yaml
# .github/workflows/deploy.yml
on: { push: { branches: [main] } }
jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions: { contents: read, id-token: write }
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v3
        with:
          workload_identity_provider: projects/PNUM/locations/global/workloadIdentityPools/github/providers/repo
          service_account: deployer@P.iam.gserviceaccount.com
      - uses: google-github-actions/deploy-cloudrun@v3
        with: { service: payhawk-review, region: europe-west1, source: ./ }
```

**Deployer SA roles.** Using `source:` runs Cloud Build under the hood, the same as `gcloud run deploy --source` ([deploying-source-code](https://docs.cloud.google.com/run/docs/deploying-source-code)):
- `roles/run.sourceDeveloper` and `roles/serviceusage.serviceUsageConsumer` on the project.
- `roles/iam.serviceAccountUser` on the runtime SA.
- The Cloud Build SA (Compute default by default) needs `roles/run.builder`.
- The Artifact Registry repo `cloud-run-source-deploy` is auto-created per region.
- If you build and push yourself instead (`docker push` + `image:`), the deployer needs `roles/run.admin` (per the deploy-cloudrun README), `roles/artifactregistry.writer` ([AR access](https://docs.cloud.google.com/artifact-registry/docs/access-control)), and `serviceAccountUser`.
- For the Job, add one more step: `gcloud run jobs deploy poll --source .` or reuse the built image. Whether `jobs deploy --source` is supported is **UNVERIFIED**.

**Cloud Build triggers** are equivalent but need a GitHub App connection in the console and keep the config away from the repo's Actions. Choose them only if you want no GitHub-side CI.

**Manual / first deploy:** `gcloud run deploy payhawk-review --source . --region R --service-account app@P.iam.gserviceaccount.com --no-allow-unauthenticated`. It uses your Dockerfile if one is present, otherwise buildpacks. `pyproject.toml` support for buildpacks has been GA since 2026-01-13 ([release notes](https://docs.cloud.google.com/run/docs/release-notes)).

---

## 7. Minimal Dockerfile (uv, Python 3.12)

Cloud Run injects `PORT` (default 8080). The server must bind `0.0.0.0`. You get SIGTERM, then SIGKILL after 10 s. Images must be linux/amd64 (build with `--platform linux/amd64` on Apple Silicon) ([container contract](https://docs.cloud.google.com/run/docs/container-contract)). This is adapted from [uv Docker guide](https://docs.astral.sh/uv/guides/integration/docker/) (astral.sh, not Google). The BuildKit cache mounts are dropped because BuildKit support in Cloud Build's default docker step is **UNVERIFIED**.

```dockerfile
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project
COPY . .
RUN uv sync --locked
ENV PATH="/app/.venv/bin:$PATH"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
```
`exec` makes uvicorn PID 1 so it receives SIGTERM. Add `.venv` to `.dockerignore`. The pip variant replaces the two `uv sync` lines with `pip install --no-cache-dir -r requirements.txt` before `COPY . .`. Pin the uv tag to whatever version is current; the `0.9` tag is **UNVERIFIED**.

---

## 8. Auth for the internal UI

- **IAP directly on Cloud Run** has been **GA since 2026-03-13** with no load balancer needed ([release notes](https://docs.cloud.google.com/run/docs/release-notes), [IAP for Cloud Run](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run)):
  ```bash
  gcloud run deploy payhawk-review --source . --region R --no-allow-unauthenticated --iap
  gcloud run services add-iam-policy-binding payhawk-review --region R \
    --member serviceAccount:service-PNUM@gcp-sa-iap.iam.gserviceaccount.com --role roles/run.invoker
  gcloud iap web add-iam-policy-binding --resource-type=cloud-run --service payhawk-review --region R \
    --member user:finance@customer.com --role roles/iap.httpsResourceAccessor
  ```
  The flag spelling `--resource-type=cloud-run` is **UNVERIFIED**; the page names `gcloud iap web add-iam-policy-binding`. Constraints: users outside your org need an *External* OAuth consent screen, and orgless projects must enable IAP once in the console first. IAP replaces the caller identity, which is why section 1 recommends a Job for scheduling. Cost is not stated on the page (**UNVERIFIED**; IAP has historically been free).
- **Basic auth** (FastAPI `HTTPBasic` + `secrets.compare_digest`, password in Secret Manager) works the same locally and on Cloud Run, but the service has to be public (`--allow-unauthenticated`). You also own password sharing, rotation, and the lack of per-user audit.

**Recommendation: IAP.** It is a single flag, adds no code, gives per-user Google login, and works for customer users through the External consent screen. Locally, skip auth on `localhost` (`AUTH_DISABLED=1`). Optionally read `X-Goog-Authenticated-User-Email` for audit; validating the IAP JWT header is optional because IAP is enforced before the container.
