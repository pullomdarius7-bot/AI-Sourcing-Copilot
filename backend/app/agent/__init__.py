"""Sourcing agent.

Layers:
  prompts    - versioned system prompt and output schemas (the prompt layer)
  sources    - source registry; every statement in an answer points at one of these
  retrieval  - PostgreSQL full-text index over document text, plus entity lookups (the retrieval layer)
  glossary   - built-in, citable sourcing reference and the app's own scoring method
  tools      - read-only tools with per-tool permissions; numbers come from deterministic services (the tool layer)
  planner    - deterministic intent router; the model planner is validated against the same tool registry
  composer   - deterministic answer writer from tool facts
  llm        - optional model planner and writer (Anthropic, OpenAI, local) behind strict schemas
  safety     - input checks, prompt-injection handling, citation and number grounding (the safety layer)
  memory     - conversation history and focus entities (the memory layer)
  framework  - plan -> act -> compose -> verify -> persist (the agent framework)
  audit      - run records, audit log entries, metrics (the audit layer)
  reports    - stored, cited reports with PDF and Markdown export
"""
