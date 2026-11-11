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

## Sprint 4: suppliers, RFQs and comparison

No new services. RFQ packages are built by the API and sent by the existing workers (`rfq.send_package` jobs) through
the configured SMTP server (Mailpit by default, so nothing leaves the host until you point SMTP_* at a real relay).

**Supplier database.** Profiles (code `SUP-0001`, legal and commercial details, tags), categories with sub-categories,
contacts with exactly one primary, CSV export (formula-injection safe), and a full history per supplier.

**Qualification.** Each organization gets a default checklist (quality certification, sanctions screening, code of
conduct are required; insurance, financial stability and references are scored). Buyers record checks with notes and
expiry dates; only approvers can waive, with a written reason. A supplier is qualified only after an approver, who
did not submit it, signs off with every required check passed or waived. Expired evidence or a failed required check
makes a supplier stop counting as qualified immediately, and recording a failed required check sends it back to review.

**RFQ generation and approval.** An RFQ (`RFQ-2026-0001`) is created blank or from an approved requirement extraction:
rejected items are dropped, verified quantities become line quantities, and values the document did not state are
sent to suppliers as explicit questions, never filled in. Draft -> submit -> approve -> send. Submitting freezes a
SHA-256 of exactly what will be sent and to whom; the approver must not be the creator or submitter, every invited
supplier must be qualified at submit, approve and send time, and the database refuses `sent` without an approval.
Approvers can return an RFQ to draft (new version) with a reason.

**Email packages.** One RFC 5322 message per supplier (primary contact in To, others in Cc, the sender as Reply-To)
with the RFQ PDF and a machine-readable JSON attached, stored in MinIO/local storage and downloadable as `.eml` for
manual sending. The send job checks the package's SHA-256 and its binding to the approved content before sending, so
a modified package is never sent. Failures retry, then dead-letter and can be re-sent.

**Exports.** RFQ PDF (watermarked DRAFT until approved, content fingerprint in the footer) and JSON; comparison PDF
and Excel; supplier CSV. Every export is in the audit log.

**Quotes and comparison.** Quotes are entered by hand or created from an analysed quote document (only grounded values
are copied; unknowns stay empty). Quote lines are matched to RFQ lines by suggestion, and a person confirms each match
and verifies the quote. The comparison engine scores verified quotes on price (on the basket of lines every supplier
priced), lead time, coverage (mandatory lines count double) and qualification, with adjustable weights and a written
explanation for every score. It never converts currencies or units, never fills gaps (an unknown lead time scores 0
and says so), flags abnormally low prices, arithmetic mismatches, differing Incoterms, expired or incomplete quotes
and near-ties, and lists suppliers it did not score with the reason. Comparisons are stored immutably with a
fingerprint of their inputs.

**Award.** The engine ranks; a person decides. An approver (not the RFQ's creator) awards a ranked, currently
qualified supplier against a comparison that is still current, with a written reason. Choosing someone other than the
top score is allowed and recorded as such. The database refuses an `awarded` RFQ without supplier, approver,
comparison and reason.

Roles: everyone reads; buyers manage suppliers, RFQs and quotes and run comparisons; approvers qualify suppliers,
approve RFQs, run comparisons and award; admins can do everything; auditors read and export.

Assumptions: one quote per supplier per RFQ (revisions edit it, with history); no FX or unit conversion is ever
assumed; RFQ due dates are calendar dates; the sender address is EMAIL_FROM for all organizations on one deployment.

## Sprint 5: quote analysis and award approval

No new services and no new dependencies. Run `make migrate` (or restart the stack) to apply migration `0005`. All
analysis is plain arithmetic in `backend/app/analysis/` using exact decimals; no AI model takes part in any
calculation. Formulas, deviation codes and risk points are in [docs/ANALYSIS.md](docs/ANALYSIS.md).

**Quote ingestion.** Besides manual entry and analysed documents, a supplier's price sheet (CSV or XLSX, up to
`QUOTE_IMPORT_MAX_MB`) can be imported as a draft quote. Columns are recognised by header name (Description,
Quantity, Unit, Unit price, Total, Lead time, MOQ, Currency, RFQ line; common German headers too). Numbers are read
exactly: the file's decimal separator is worked out from its own unambiguous numbers, and a value such as `1.000`
that could mean one or a thousand is left empty with a warning rather than guessed. An "RFQ line" number only becomes a
suggested match. Files are malware-scanned first. The quote stays a draft until a person confirms the matches and
verifies it.

**Normalization.** For each RFQ line and supplier the engine converts the quoted unit to the requested unit (exact
standard factors for count, mass, length, area and volume; anything across dimensions, such as box -> pcs or pcs -> kg,
only with a rule recorded under *Rates & units*), derives a unit price from a total only when the quoted quantity is
stated, and converts the currency with the organization's latest recorded rate valid on the analysis date (direct or
inverted, never through a third currency). A quote whose currency has no rate is listed as not scored.

**Price, lead time and MOQ analysis.** The payable quantity is the larger of the requested quantity and the MOQ, so a
supplier whose MOQ forces a larger order is compared on what would actually be paid, and the extra cost is shown.
Price is compared on the basket of lines every scored supplier priced. Lead time is the longest priced line, compared
with the fastest supplier and, when the RFQ has a *delivery required by* date, with the days available.

**Deviation detection and risk.** Each quote is checked against the request: lines not quoted (by priority), units or
currencies that cannot be converted, quantity differences, MOQ above quantity, missing prices, arithmetic mismatches,
low-confidence values, abnormally low prices, Incoterms or payment terms differing from what the RFQ requested,
expired or expiring quotes, missing validity, late delivery and unqualified suppliers. Every deviation has a severity;
risk is the sum of points (critical 25, major 10, minor 3, capped at 100), and its level and contributing factors are
shown.

**Scoring.** Weighted sum of price, lead time, coverage, qualification and risk (default 45/20/15/10/10, adjustable per
run), with an explanation for every criterion. Changing a quote, an exchange rate, a unit rule, a requested term or a
supplier's qualification makes earlier comparisons outdated.

**Human approval workflow.** The direct award endpoint is replaced by recommendation and approval. A buyer recommends a
supplier from a current comparison with a rationale; a different person with approval rights (not the recommender,
not the RFQ creator) approves or rejects it. Approval re-checks that the comparison is still current and the supplier
still qualified, and needs a written reason when the recommendation is not the top score; only then is the RFQ
awarded. One recommendation can be pending per RFQ; it can be withdrawn by its author, and cancelling the RFQ withdraws
it. The database enforces four-eyes approval and a note on every rejection.

**RFQ terms.** RFQs gain *delivery required by*, *Incoterms requested* and *payment terms requested*. When set, they are
printed on the RFQ PDF and in the supplier email and are part of the approved content. RFQs approved earlier keep their
approval fingerprint.

**Exports.** The comparison PDF adds lead time and MOQ, and risk and deviations sections; the Excel export adds risk,
on-time and MOQ columns and a Deviations sheet. Comparisons stored by Sprint 4 still render.

Roles: approvers and admins maintain exchange rates and unit rules (`reference:manage`); everyone who can read
comparisons can see them. Buyers and approvers can recommend (`comparisons:run`); approvers and admins decide
(`rfqs:approve`).

Settings: `FX_MAX_AGE_DAYS` (30), `QUOTE_VALIDITY_WARNING_DAYS` (7), `QUOTE_IMPORT_MAX_MB` (5).

Assumptions: exchange rates are entered by people (from treasury or a bank), never fetched; the analysis date is the
UTC date of the run; lead time for a quote is its slowest priced line; a missing unit on a quoted line is taken as the
requested unit and flagged; freight and other unmatched lines are listed but not added to prices.

## Sprint 6: sourcing assistant and monitoring

Run `make env-upgrade` on an existing install (adds `METRICS_TOKEN` and `GRAFANA_ADMIN_PASSWORD` to `.env` without
touching existing values), then `make up`. Migration `0006` runs automatically. No new services are required and no new
Python or npm dependencies were added.

**The assistant works as soon as the stack is up.** Open *Assistant* in the app. With no AI provider configured it runs
in deterministic mode: fixed rules choose the tools, and answers are assembled from the tools' own statements. With a
provider configured (Anthropic, OpenAI or a local Ollama model, as in Sprint 2) and `AGENT_MODE=auto`, the model plans
the tool calls and words the answer, and the safety layer checks every statement before it is shown.

What it does: answers sourcing questions, analyses an RFQ, compares suppliers, explains a supplier's score criterion by
criterion, summarizes documents (verbatim key passages plus the grounded fields of the document analysis), recommends
next actions from workflow state (per RFQ or across the work queues) and generates stored reports with PDF and Markdown
export.

Guarantees, enforced in code and tests:
- **Every answer cites sources.** Each statement carries source ids (S1, S2, …); each source names what it is, links to
  the page it came from and carries a SHA-256 of its content. A statement without a valid source is never shown.
- **No fabricated figures.** All numbers come from the deterministic services (analysis engine, qualification state,
  stored records). Any number in a model-written statement that is not in the sources it cites causes the whole model
  answer to be rejected and replaced by the deterministic answer; the rejection is recorded.
- **Unknown stays unknown.** What the tools cannot find is listed under *Unknown*, never filled in.
- **Every response is traceable.** Each answer links to an append-only run record: the question, plan (and who made it:
  rules or model), every tool call with its arguments, sources and timing, every model call with prompt and response
  fingerprints, safety flags, prompt version and hash, and an answer hash. Runs are in the audit log as `agent.answered`.
- **Read-only.** The assistant cannot award, approve, send, edit or delete; such requests are refused and pointed to the
  right page. Reports are its only write, into its own table.
- **Permission-aware and tenant-isolated.** Each tool checks the asking user's permissions and reads through the same
  row-level security as the rest of the app. Conversations are private to their author; auditors and admins can read
  every run (`agent:audit`).
- **Prompt-injection resistant.** Document text is passed to a model only as fenced, labelled data; instruction-like text
  in a source is flagged on the run. The deterministic path cannot be steered by document content at all.

Retrieval is PostgreSQL full-text search (a generated `tsvector` column with a GIN index) over document text, so there is
no vector database or embedding service to run. Documents are indexed by the worker when processing finishes; documents
processed before this sprint are indexed during the first searches (`AGENT_INDEX_ON_DEMAND_DOCS` per search), or all at
once with `POST /api/v1/agent/reindex` (admins). General terms (all eleven Incoterms 2020 rules, MOQ, lead time, payment
terms, TCO, …) and the app's own scoring, risk, conversion and award methods come from a built-in, versioned reference;
the method entries are generated from the analysis engine's constants.

Architecture and API details: [docs/AGENT.md](docs/AGENT.md).

**Monitoring.** The API exposes Prometheus metrics at `/metrics` (inside the compose network; bearer `METRICS_TOKEN`;
disabled in production without a token; never proxied by the web front end). `make monitoring` starts Prometheus
(127.0.0.1:9090) with alert rules and Grafana (127.0.0.1:3001, user `admin`, password `GRAFANA_ADMIN_PASSWORD`) with a
provisioned dashboard: API traffic, errors and latency, assistant answers by outcome, latency, tool calls, safety events,
model tokens and ratings, job queue and documents. Details: [docs/MONITORING.md](docs/MONITORING.md).

Settings: `AGENT_MODE` (auto | deterministic | llm), `AGENT_MAX_TOOL_CALLS` (4), `AGENT_RATE_PER_MINUTE` (20 per user),
`AGENT_INDEX_ON_DEMAND_DOCS` (20), `METRICS_TOKEN`, `GRAFANA_ADMIN_PASSWORD`, `PROMETHEUS_RETENTION` (15d).

Assumptions: the assistant answers in English; it does not learn across conversations (memory is the conversation's own
history and the entities it is about); full-text search uses PostgreSQL's language-neutral `simple` configuration so it
works for any language without stemming; Prometheus alert rules are evaluated and visible in Prometheus and Grafana, and
delivery (email, chat) is left to an Alertmanager you add, since none is required to run the stack.

## Operations

Everything needed to run this in production is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): server preparation, the
nginx + Let's Encrypt overlay (`docker-compose.prod.yml`), releases and rollback (`scripts/deploy.sh`,
`scripts/rollback.sh`), the GitHub Actions deployment pipeline, backup, restore and restore tests, monitoring and logging
(compose profiles `monitoring` and `logging`), health checks, load testing (`loadtest/k6.js`), disaster recovery, and the
security and readiness checklists.

Note: the web front end is now published on 127.0.0.1 only (`FRONTEND_BIND`). On a development machine nothing changes
(http://localhost:3000). To reach it from other machines without a reverse proxy, set `FRONTEND_BIND=0.0.0.0`.

## Phase 1: account types and onboarding

Registration is open: anyone can create an account and chooses what it is.

| Account type | Who it is for | Tenancy |
|---|---|---|
| `solo_buyer` | One person sourcing for their own resale business | An organization of one; invitations are refused |
| `org_buyer` | A procurement team | Members, the five roles and approvals described above |
| `supplier` | A wholesaler or liquidator listing inventory | Own organization; its staff are invited like any team |

- The account type is stored on the organization (`organizations.account_type`), next to `onboarding_completed`,
  `onboarding_completed_at` and `onboarding_profile` (JSONB). Roles inside an account are unchanged.
- After verifying the email and signing in, the first administrator is sent to `/onboarding`: business type,
  monthly sourcing budget (buyers) or monthly inventory volume (suppliers), team size (buying teams) and product
  categories. The form is defined in `backend/app/onboarding.py` and served by `GET /api/v1/onboarding`; answers
  go to `PUT /api/v1/onboarding` and only listed options are stored.
- Until the form is submitted every feature endpoint answers `403 onboarding_required`. Sign-in, sessions,
  password and two-factor endpoints keep working. Invited members never see the form.
- Isolation is unchanged: every tenant table carries `organization_id` under forced row-level security, and a
  test fails if a table with that column is added without it. The profile lives on the organization row, which
  a tenant can only read for itself.
- Migration `0008` adds the columns; organizations that already exist become `org_buyer` with onboarding
  completed. `alembic downgrade 0007` removes the columns again.
