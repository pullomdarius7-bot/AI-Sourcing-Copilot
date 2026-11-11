# Sourcing assistant

Code: `backend/app/agent/`. API: `backend/app/routers/agent.py`. UI: `frontend/src/app/(app)/assistant/`.

## Request flow

```
question
  │  safety.check_question      refuse action requests, flag injection patterns, length limit
  │  memory                     conversation focus (RFQ, supplier, document) + recent turns
  ▼
plan        rules (planner.py)  ──or──  model (llm.plan) validated against the tool registry and argument schemas
  ▼
act         tools.execute       one savepoint per tool; permission check; facts + sources; errors become unknowns
  ▼
compose     composer.compose    deterministic blocks from facts
            llm.write           (model mode) blocks from the sources only
  ▼
verify      safety.validate     each statement cites known sources; each number appears in what it cites;
                                no markup or links; rejected model output -> deterministic answer (status "fallback")
  ▼
persist     agent_runs (append-only trace) · agent_messages · audit log "agent.answered" · metrics
```

## Layers

| Layer | Module | Responsibility |
| --- | --- | --- |
| Agent framework | `framework.py` | Mode choice, plan → act → compose → verify → persist; failures are recorded as runs with status `error`. |
| Memory | `memory.py` | Last turns (model planner context only, never a source); entities in focus so "this RFQ" resolves. |
| Retrieval | `retrieval.py`, `text.py` | Chunking (≈1,500 characters, overlap, word boundaries), PostgreSQL full-text ranking, entity lookup by number, code, name or file name. |
| Prompt | `prompts.py` | Versioned system, planner and writer prompts; strict JSON schemas; `prompt_sha256` stored on every run. |
| Tools | `tools.py` | Read-only tools with per-tool permissions. Figures come from the analysis engine and stored records. |
| Audit | `audit.py`, `agent_runs` | Per-user rate limit, audit log entry, metrics, complete run trace. |
| Safety | `safety.py` | Input checks, source injection flags, citation and number grounding of every statement. |
| Reports | `reports.py`, `agent_reports` | Stored, cited reports; PDF and Markdown render the stored record exactly. |
| Reference | `glossary.py` | Built-in, versioned sourcing reference; method entries generated from engine constants. |

## Tools

| Tool | Permission | Returns |
| --- | --- | --- |
| `search_knowledge` | per source: documents, rfqs, suppliers | ranked document passages, reference entries, matching RFQs and suppliers |
| `rfq_overview` | rfqs:read | status, dates, requested terms, lines, invited suppliers and quote status |
| `compare_suppliers` | rfqs:read, comparisons:read | live engine analysis (not stored): ranking, baskets, lead time, MOQ, risk, deviations, not scored |
| `explain_score` | rfqs:read, comparisons:read | a supplier's criterion-by-criterion explanation and deviations, or the scoring method |
| `supplier_profile` | suppliers:read | status, qualification state, categories, contacts, terms, RFQ history |
| `document_summary` | documents:read | metadata, document-analysis fields (with their verbatim quotes), extractive key passages, requirement extraction status |
| `next_actions` | rfqs:read (+ per queue) | open steps for one RFQ from workflow state, or counts in the work queues the user can see |
| `lookup_term` | – | reference entries |
| `generate_report` | as the tools it uses | stores an `agent_reports` record and returns it as a source |

## Sources and statements

A source is `{id, kind, title, path, ref, as_of, excerpt, sha256}`; its full content is exactly the statements the tool
made from it (or the verbatim passage), which is what grounding checks against. An answer is a list of blocks
`{type: text|heading, text, sources}` plus `unknowns` and `follow_ups`. The Markdown `content` of a message is rendered
from these, with `[S1]` markers.

## Modes

| `AGENT_MODE` | Behaviour |
| --- | --- |
| `auto` (default) | model mode when a provider is configured, otherwise deterministic |
| `deterministic` | never calls a model, even if one is configured |
| `llm` | model mode; without a provider it runs deterministically and flags `llm_mode_without_provider` |

Model calls go through the providers from Sprint 2 (`AI_PROVIDER_ORDER`), with failover. External providers receive the
same redaction as document analysis (payment-card and US social security numbers). The model sees the question, the
recent turns (as context, not as a source) and the sources from this question's tool calls; nothing else from the
database.

## API (`/api/v1/agent`)

| Method and path | Who | Purpose |
| --- | --- | --- |
| `GET /status` | agent:use | mode, providers, prompt version and hash, tools, index coverage |
| `POST /ask` `{question, conversation_id?}` | agent:use | ask; starts a conversation when none is given |
| `GET/POST /conversations`, `GET/PATCH /conversations/{id}` | owner | list, create, read, rename, archive |
| `POST /conversations/{id}/messages` `{question}` | owner | ask in a conversation |
| `POST /messages/{id}/feedback` `{rating: 1 or -1, note?}` | owner | rate an answer |
| `GET /runs/{id}` | owner or agent:audit | full trace |
| `GET /runs?status=&mode=&user_id=` | agent:audit | all runs in the organization |
| `GET /reports`, `GET /reports/{id}`, `/pdf`, `/markdown` | creator or agent:audit | reports and exports |
| `POST /reindex` | org:manage | rebuild the document index |

## Data

`knowledge_chunks` (generated `tsvector`, GIN index), `agent_conversations`, `agent_messages` (assistant messages must
reference a run), `agent_runs` and `agent_reports` (append-only for the application role). All have forced row-level
security on `organization_id`.

## Run statuses

`answered` · `fallback` (model output rejected or unavailable; the deterministic answer was shown) · `refused` (action
request) · `clarify` (which RFQ/supplier/document?) · `error` (a failure; nothing was answered, the trace says why).
