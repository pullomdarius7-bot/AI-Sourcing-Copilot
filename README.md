# AI Sourcing Copilot

Sprint 1: native authentication, tenant isolation, RBAC. Sprint 2: document upload, background workers, AI layer.
No third-party identity provider, no required SaaS.

## Deploy

    make init          # creates .env with random secrets (or: cp .env.example .env and edit)
    docker compose up -d --build --wait

- App: http://localhost:3000
- Mail inbox (Mailpit, captures verification/reset emails): http://localhost:8025
- MinIO console: http://localhost:9001 (credentials: MINIO_ROOT_USER / MINIO_ROOT_PASSWORD in .env)
- API (localhost only): http://127.0.0.1:8000/api/v1/health

HTTPS: set DOMAIN and APP_BASE_URL, set COOKIE_SECURE=true, then `docker compose --profile tls up -d`.
Real email: point SMTP_* in .env at your own SMTP relay.

## Verify

    make test     # backend suite (auth, MFA, RBAC, tenant isolation, API) against a throwaway DB
    make smoke    # register -> verify -> login -> me -> logout through the frontend proxy
    make lint

## Design

- Passwords: argon2id. Access token: JWT (15 min, HS256). Refresh token: opaque, rotated on every use,
  stored hashed, reuse after a 10 s grace revokes the session. Every request checks the session row,
  so logout, deactivation and password reset take effect immediately.
- MFA: TOTP with replay protection, seeds encrypted at rest (Fernet), 10 single-use recovery codes.
- Lockout: 5 failures -> 15 minutes. Per-IP rate limit on unauthenticated endpoints.
- Tenancy: organization_id on every tenant table plus PostgreSQL row-level security (FORCE). The API
  connects as `copilot_app` (no superuser, no BYPASSRLS); migrations run as the admin role.
  `audit_logs` is append-only for the app role.
- Browser: JWTs live in httpOnly cookies set by the Next.js proxy (`/api/*`); cross-origin writes are rejected.

## Assumptions

1. One user belongs to one organization; email is globally unique.
2. Pre-auth lookups (login, token redemption) use a server-only `app.system` setting, never user-controlled.
3. Domain permissions for sourcing features are added in later sprints; Sprint 1 defines org, users, invitations, audit.
4. package-lock.json is generated on first `npm install` (the Dockerfile handles both cases).

## Sprint 2: documents, workers, AI

Services: `backend` (API), `worker` (job processor), `minio` (object storage), `clamav` (virus scanner), `ollama` (optional).

    make workers N=3                          # scale background workers
    docker compose --profile local-llm up -d  # optional self-hosted LLM, then pull a model:
    docker compose exec ollama ollama pull llama3.1:8b   # and set LOCAL_LLM_URL=http://ollama:11434 in .env

Pipeline (each step is its own retryable job): upload -> `document.scan` -> `document.extract` -> `document.analyze`.

- Upload: PDF, DOCX, XLSX, CSV, TXT, PNG, JPG up to MAX_UPLOAD_MB. File content is sniffed (the extension and the
  declared MIME type must agree with it), hashed with SHA-256, and identical files in the same organization are
  detected and not stored twice. Archives are checked for bombs; XML is parsed with entity expansion disabled.
- Virus scan: built-in checks (EICAR, executables, PDF active content, Office macros/embedded objects) plus ClamAV
  over the clamd protocol. A scanner outage never counts as "clean": the job retries. Infected files are
  quarantined, deleted from storage and can never be downloaded or analyzed.
- Storage: MinIO first; if it is unreachable, writes automatically fall back to the shared local volume. Each
  document remembers where its bytes live, so reads keep working through MinIO outages and recoveries.
- Jobs: PostgreSQL queue (`FOR UPDATE SKIP LOCKED`), exponential backoff with jitter, visibility timeout that
  recovers jobs from crashed workers, dead-letter state with one-click retry, stats at `/api/v1/jobs/stats` and the
  Jobs page. Permanent failures (corrupt files, auth errors) skip retries.
- AI: providers `anthropic`, `openai` (also any OpenAI-compatible server via OPENAI_BASE_URL) and `local` (Ollama),
  tried in AI_PROVIDER_ORDER with automatic failover. Output is forced into a strict JSON schema, validated, and
  guarded: every value must be a verbatim quote from the document or it becomes an explicit `unknown`;
  numbers are checked against their quote; arithmetic mismatches and prompt-injection patterns flag the result for
  review; card/SSN patterns are redacted before text goes to an external provider. Every call is recorded in
  `ai_audit_logs` (append-only; hashes, latency, tokens, flags, never document text). With no provider
  configured, documents are still scanned and parsed and analysis is marked "skipped".

Assumptions: documents are visible to every member of the organization; the app uses the MinIO root credentials
(create a dedicated service account for stricter setups); duplicate detection is exact-content (SHA-256) per
organization; scanned images/PDFs without text are analyzed only by vision-capable models (no OCR engine is bundled).

## Sprint 3: requirement extraction and human verification

No new services: extraction runs as a `requirements.extract` job on the existing workers and needs at least one AI
provider (a self-hosted model works). Start it from a document's page, then review in **Requirements** and the
**Review queue**.

Rules enforced in code, not by prompting:

- **No hallucination.** A model's answer is checked against the document. A value is kept only if its source quote
  is found in the document text, the value appears inside that quote, any unit appears in it, and a stated priority
  (mandatory/preferred/optional) is backed by words like shall/should/may. A paraphrased requirement statement is
  replaced by the verbatim quote if its words and numbers are not in the source. Everything else is dropped or turned
  into an explicit unknown; the discarded claim is kept in `confidence_detail` for audit but never shown as a value.
- **Unknown stays unknown.** Missing parameters are stored as `unknown` with no value, no source and confidence 0.
  The database refuses an unknown parameter that carries a value (`ck_parameters_unknown_has_no_value`) and a
  known machine-extracted value without a source quote (`ck_parameters_known_has_source`).
- **Every item has a source reference, a confidence and a validation state.** The source is the quote plus its
  character offsets, line and sheet in the document text (the evidence viewer highlights it). Confidence is the model's
  own score multiplied by deterministic factors (exact vs normalised match, hedged wording such as "approximately",
  very short or very long quotes, unit missing from the quote, parameter outside its requirement's quote) and capped
  at 0.5 when the document contradicts itself (for example two different Incoterms). Validation state is
  `unverified`, `verified`, `corrected` or `rejected`; only people change it.
- **Corrections need evidence.** A reviewer's correction must quote the document (re-checked by the same rules) or give
  "other evidence" with an explanation; both are recorded with the machine's original value, which "Undo review" restores.
- **Four-eyes approval.** An extraction can be approved only when every item is resolved, by an approver who neither
  started it nor reviewed any of its items (`REQUIRE_SEPARATE_APPROVER`). Approval locks it; approved extractions
  protect their document from deletion and can be exported as JSON (rejected items excluded, unknowns preserved).
- **Audit.** Each action is written to the organization audit log and to an append-only `review_events` history with
  before/after snapshots; every model call is in `ai_audit_logs`.

Roles: everyone reads; requester, buyer and admin start extractions; buyer, approver and admin review; approver and
admin approve; auditor is read-only. Assumptions: evidence offsets refer to the document's extracted text (scanned
images without a text layer cannot be grounded and yield no requirements); bulk verification is limited to values
that are grounded, unflagged and at least 85% confident; the model's parameter vocabulary is open (snake_case names).
