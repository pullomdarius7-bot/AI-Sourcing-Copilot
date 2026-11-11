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

## Phases 1-2: public site, account types, onboarding and trial credits

**Public landing page.** `/` is the public marketing page (hero, who it is for, comparison, how it works, pricing).
Signed-in visitors are sent to `/dashboard`. Its calls to action link to `/register?tier=trial`,
`/register?role=org_buyer` and `/register?role=supplier`. Copy and prices live in
`frontend/src/components/landing/`.

**Registration.** Open to anyone: name, email and password. The `role` link parameter only pre-selects a role.

**Onboarding wizard (`/onboarding`).** After verifying the email and signing in, the first administrator of an
account is sent through three steps. Until it is finished every feature endpoint answers
`403 onboarding_required`; sign-in, sessions, password and two-factor endpoints keep working.

1. Role: `solo_buyer`, `org_buyer` or `supplier`. Fixed once onboarding is complete.
2. Categories and scale. Buyers: product categories, monthly purchasing budget, preferred lot sizes. Suppliers:
   inventory types, typical manifest format, dispatch regions. Team and supplier accounts also name their company.
3. Provisioning: the answers are saved, `onboarding_completed` is set, the free trial starts and the user lands
   on the dashboard.

The roles, questions and allowed answers are defined in `backend/app/onboarding.py`, served by
`GET /api/v1/onboarding` and submitted to `PUT /api/v1/onboarding`; only listed options are stored.

| Account type | Who it is for | Tenancy |
|---|---|---|
| `solo_buyer` | One person sourcing for their own resale business | An organization of one; invitations are refused |
| `org_buyer` | A procurement team | Members, the five roles and approvals described above |
| `supplier` | A wholesaler or liquidator listing inventory | Own organization; its staff are invited like any team |

**Data model.** The account type, `onboarding_completed`, `onboarding_completed_at` and `onboarding_profile`
(JSONB) are columns of `organizations` (migration `0008`). Organizations that existed before became `org_buyer`
with onboarding completed and no trial.

**Credits and trial.** One credit is the unit everything is priced in. `credit_transactions` is the record of
every grant and charge; `organizations.credit_balance` is the running balance. Both are changed only by
`backend/app/billing/ledger.py`, in one transaction, with the organization row locked (`SELECT ... FOR UPDATE`):
the balance cannot go below zero and concurrent charges cannot overspend. Finishing onboarding grants
`TRIAL_CREDITS` (10) and sets `trial_ends_at` to `TRIAL_DAYS` (7) from now, once per account.

**Isolation.** Every tenant table carries `organization_id` under forced row-level security, and a test fails if
a table with that column is added without it. `alembic downgrade 0007` removes everything in this section and the next.

## Phase 3: Stripe billing, credit metering and the AI gateway

**The gateway.** `backend/app/ai/gateway.py` is the only code that calls a model provider (a test fails if any
other file does). Every AI action goes through four steps:

1. `acquire`: lock the account, check it has an active subscription or an unexpired trial, check the balance
   covers the cost, deduct the credits and commit. If a check fails nothing is charged, no provider is
   contacted, and the caller gets HTTP 402:
   `{"error": {"code": "INSUFFICIENT_CREDITS" | "SUBSCRIPTION_REQUIRED", "remaining": 0, "upgrade_url": "/billing", ...}}`
2. `dispatch`: the model call (one charge can cover several calls, e.g. the chunks of a long document).
3. `settle`: on success, in the transaction that stores the result.
4. `release`: on failure the credits are refunded. If a worker dies mid-call the charge stays `held` and the
   retry of the same document or run reuses it instead of charging again.

| Action | Cost | Where |
|---|---|---|
| `manifest_extraction` | 1 credit under 500 line items, otherwise 1 per 100 line items | Analysis of every uploaded document |
| `deep_analysis` | 1 credit per run (`CREDIT_COST_DEEP_ANALYSIS`) | Requirement extraction; answers 402 before a job is queued |
| `assistant_answer` | 1 credit (`CREDIT_COST_ASSISTANT_ANSWER`) | Model-written assistant answers; without credits the assistant answers from fixed rules and no model is used |

An upload is always accepted, scanned and made searchable; without credits only its AI analysis is skipped,
with the reason shown on the document. `BILLING_ENFORCED=false` switches all checks off.

**Plans.** `solo_buyer_monthly` ($79, 25 credits), `enterprise_buyer_monthly` ($249, 250 credits, turns a solo
account into a team) and `supplier_monthly` ($149, 100 credits). Buyers can choose either buyer plan; suppliers
the supplier plan. Each paid invoice resets the balance to the plan's quota: unused credits do not roll over.

**Stripe setup.**

1. In Stripe create one recurring monthly price per plan and set its lookup key to the plan key (or put the
   price ids in `STRIPE_PRICE_*`).
2. Add a webhook endpoint `https://<domain>/api/webhooks/stripe` for `checkout.session.completed`,
   `invoice.payment_succeeded`, `invoice.payment_failed`, `customer.subscription.updated` and
   `customer.subscription.deleted`.
3. Set `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` in `.env`, and enable the customer portal in Stripe.

**Endpoints.** `GET /api/billing` (plan, status, balance, plans, recent transactions),
`POST /api/billing/create-checkout-session` and `POST /api/billing/create-portal-session` (administrators
only), `POST /api/webhooks/stripe` (authenticated by Stripe's signature over the raw body; events older than
five minutes or already processed are not applied again).

| Event | Effect |
|---|---|
| `checkout.session.completed` | Links customer and subscription, sets the plan; if paid: `active` and the monthly quota. With a trial: `trialing`, trial credits kept |
| `invoice.payment_succeeded` | `active`, quota refilled, period end stored. A zero-amount trial invoice grants nothing |
| `invoice.payment_failed` | `past_due`: AI is paused and the account's administrators are emailed |
| `customer.subscription.updated` | Plan and status changes made in the portal |
| `customer.subscription.deleted` | `cancelled`, plan removed, remaining credits wiped |

Checkout carries over only what is left of the account's free trial (a card is collected, the first charge
happens when the trial ends). An account whose trial credits or days are used up is charged immediately and
gets its quota immediately.

**In the app.** The header shows the credits left, the trial countdown and an "Add Credits / Upgrade" button; a
banner appears on every page at 2 credits or fewer, after the trial, after a failed payment and after
cancellation. `/billing` shows the plan, the plans available, the portal and the credit activity.

Migration `0010` renames `credit_ledgers` to `credit_transactions`, adds the action, the subject and the
held/settled/refunded state, adds the subscription columns and `stripe_events`, and starts a standard trial for
accounts that existed before trials did.

## Phase 4: two-sided marketplace, supplier ingestion and masked RFQs

**Supplier side (`/supplier/manifests`).** A supplier uploads a manifest (CSV, XLSX or PDF). It goes through the
same upload checks and virus scan as any document, then a `manifest.normalize` job turns it into lot lines:

- Rules first, always: the header row is found by column name, quantities and prices are parsed exactly, the
  condition grade (`new`, `like_new`, `customer_return`, `salvage`), category, brand, UPC, ASIN and model number
  are mapped from vocabularies, unit MSRP is derived from extended retail, and totals rows are skipped.
- Then the AI model, when one is configured and the account can pay (Phase 3 gateway, `manifest_extraction`
  pricing). It cleans titles and fills brand, category, condition and identifiers in batches. It never supplies
  quantities or prices, and each value must be traceable to its row: invented brands, identifiers or words, and
  changed model-number spellings, are rejected and flagged. Without credits the rules result is kept.
- PDF manifests have no table, so they need the model; every line it returns must quote a line of the PDF text
  that contains its numbers.
- Phone numbers, emails, links and the supplier's own company name are removed from titles.

The supplier then sets the lot type (single pallet, multi-pallet, truckload), the Buy-It-Now price, a private
reserve, the dispatch ZIP, loading conditions (liftgate, dock-high only, local pickup) and private pickup details,
and publishes. Publishing needs an active subscription or trial (402 otherwise).

**Buyer side (`/marketplace`).** Buyers filter published lots by category, condition, lot size, region, minimum
estimated margin, price and text. Each lot shows total units, total MSRP, asking price, estimated resale value and
gross margin (condition and category recovery rates, `backend/app/marketplace/valuation.py`) and the grade mix.
"Run AI Deep Analysis" costs 1 credit (`deep_analysis`, 402 without credits) and produces an itemized net profit
model: resale value per item (model estimates kept only within 2-100% of MSRP), channel fees, refurbishment,
unsellable units, freight, net profit, ROI, a maximum bid at the buyer's target margin and a buy / negotiate /
pass call.

**Masking.** Buyers never see the seller's company, people, email, phone, reserve, pickup address or full ZIP
(only region and the first three digits); sellers never see the buyer's. Each side appears under a stable alias
and earned badges ("Verified Liquidator - Midwest Hub", "10+ Lots Completed"); "Verified" means a paid
subscription. This is enforced by row-level security, not only by the API:

| Table | Who can read it |
|---|---|
| `marketplace_lots`, `lot_items` | The supplier; everyone else only while the lot is published or sold |
| `lot_private_details` (reserve, pickup) | The supplier only |
| `market_profiles` (alias, badges) | Everyone; written only by the system |
| `lot_analyses` | The buyer who paid for it |
| `rfq_threads`, `rfq_thread_events` | The two parties of the negotiation only |
| `rfq_moderation` (text before scrubbing) | The author's account only |

**Negotiation (`/messages`, `/rfq/[id]`).** "Submit Offer / Request Quote" opens a thread with a price, freight
terms (buyer or seller arranges), a target close date and custom terms. The party facing an offer can accept,
decline or counter; the author can withdraw. Offers below the private reserve are declined automatically without
revealing it. Messages and terms are scrubbed of emails (also "name at gmail dot com"), phone numbers, links,
payment links and handles, with a warning to the sender; mentions of off-platform payment are flagged. Every step
is an append-only, hash-chained event (the app role can only insert), and the page shows whether the chain
verifies. Acceptance marks the lot sold, closes other buyers' negotiations, updates the badges, and releases both
parties' company and contact details and the pickup address inside the thread. Every offer, counter, decision and
message emails the other side with masked identities only.

Migration `0011` adds the tables above and `documents.purpose`; `alembic downgrade 0010` removes them.
