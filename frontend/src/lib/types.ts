export type Role = "requester" | "buyer" | "approver" | "admin" | "auditor";
export const ROLES: Role[] = ["requester", "buyer", "approver", "admin", "auditor"];

export type AccountType = "solo_buyer" | "org_buyer" | "supplier";

export type SubscriptionStatus = "none" | "trialing" | "active" | "past_due" | "cancelled";

export interface Billing {
  plan: string | null;
  subscription_status: SubscriptionStatus;
  credit_balance: number;
  trial_ends_at: string | null;
  current_period_end: string | null;
  ai_access: boolean;
  ai_access_reason: string | null;
  can_manage: boolean;
  has_billing_profile: boolean;
  checkout_trial_days: number | null;
  purchased_credits: number;
  team_features: boolean;
  support: { email: string | null; priority: boolean };
  promo: { percent_off: number; plan: string } | null;
  topup: { available: boolean; reason: string | null; price_cents: number | null; min_credits: number; max_credits: number; available_at_or_below: number };
  seats: { used: number; limit: number | null };
  plans: { key: string; name: string; price_cents: number; monthly_credits: number; seats: number | null; topup_price_cents: number }[];
  transactions: {
    id: number;
    kind: string;
    action: string | null;
    subject_id: string | null;
    delta: number;
    balance_after: number;
    state: "held" | "settled" | "refunded";
    note: string | null;
    created_at: string;
  }[];
}

export interface OnboardingField {
  key: string;
  label: string;
  kind: "select" | "multiselect";
  options: { value: string; label: string }[];
  help: string;
}

export interface OnboardingState {
  account_type: AccountType;
  organization_name: string;
  completed: boolean;
  can_edit: boolean;
  roles: { value: AccountType; label: string; description: string }[];
  forms: Record<AccountType, OnboardingField[]>;
  profile: Record<string, string | string[]>;
  credit_balance: number;
  trial_ends_at: string | null;
}

export interface Me {
  id: string;
  organization_id: string;
  organization_name: string;
  account_type: AccountType;
  onboarding_completed: boolean;
  credit_balance: number;
  trial_ends_at: string | null;
  plan: string | null;
  subscription_status: SubscriptionStatus;
  ai_access: boolean;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  email_verified: boolean;
  mfa_enabled: boolean;
  last_login_at: string | null;
  created_at: string;
  permissions: string[];
}

export interface TeamUser {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  email_verified: boolean;
  mfa_enabled: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface Invitation {
  id: string;
  email: string;
  role: Role;
  expires_at: string;
  created_at: string;
}

export interface SessionInfo {
  id: string;
  user_agent: string | null;
  ip: string | null;
  created_at: string;
  last_used_at: string;
  expires_at: string;
  current: boolean;
}

export interface AuditEntry {
  id: number;
  actor_email: string | null;
  action: string;
  ip: string | null;
  meta: Record<string, unknown>;
  created_at: string;
}

export interface JobBrief {
  id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "dead";
  attempts: number;
  max_attempts: number;
  run_at: string;
  last_error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface DocumentItem {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  storage_backend: string | null;
  status: "uploaded" | "scanning" | "extracting" | "analyzing" | "processed" | "quarantined" | "failed";
  scan_status: "pending" | "clean" | "infected";
  scan_engine: string | null;
  scan_detail: string | null;
  analysis_status: "pending" | "succeeded" | "needs_review" | "skipped" | "failed";
  file_metadata: Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string;
  processed_at: string | null;
}

export interface DocumentDetail extends DocumentItem {
  text_preview: string | null;
  jobs: JobBrief[];
}

export interface Field<T> {
  value: T | null;
  unknown: boolean;
  confidence: number;
  source: string | null;
  verified: boolean;
}

export interface LineItem {
  description: string;
  source: string;
  quantity: Field<number>;
  unit: Field<string>;
  unit_price: Field<number>;
  total_price: Field<number>;
  lead_time_days: Field<number>;
}

export interface AnalysisResult {
  document_type: string;
  summary: string;
  supplier_name: Field<string>;
  currency: Field<string>;
  incoterms: Field<string>;
  payment_terms: Field<string>;
  valid_until: Field<string>;
  line_items: LineItem[];
  overall_confidence: number;
  missing_fields: string[];
}

export interface Analysis {
  id: string;
  provider: string;
  model: string;
  prompt_version: string;
  result: AnalysisResult;
  flags: string[];
  needs_review: boolean;
  created_at: string;
}

export interface JobStats {
  queued: number;
  running: number;
  succeeded: number;
  dead: number;
  oldest_queued_age_seconds: number | null;
  succeeded_last_hour: number;
  avg_duration_ms_last_hour: number | null;
  active_workers: string[];
  by_kind: { kind: string; status: string; count: number }[];
}

export interface JobItem extends JobBrief {
  queue: string;
  payload: Record<string, unknown>;
  locked_by: string | null;
  created_at: string;
}

export interface AiAuditEntry {
  id: number;
  document_id: string | null;
  provider: string;
  model: string;
  purpose: string;
  status: string;
  latency_ms: number;
  input_tokens: number | null;
  output_tokens: number | null;
  flags: string[];
  error: string | null;
  created_at: string;
}

export type ValidationState = "unverified" | "verified" | "corrected" | "rejected";

export interface Evidence {
  source_quote: string | null;
  source_start: number | null;
  source_end: number | null;
  source_line: number | null;
  source_section: string | null;
  confidence: number;
  model_confidence: number;
  confidence_bucket: "high" | "medium" | "low";
  confidence_detail: Record<string, unknown>;
  grounding_status: "grounded" | "unknown" | "manual";
  flags: string[];
  validation_state: ValidationState;
  verified: boolean;
  verified_by: string | null;
  verified_at: string | null;
  review_note: string | null;
  original: Record<string, unknown> | null;
}

export interface ParameterItem extends Evidence {
  id: string;
  run_id: string;
  requirement_id: string | null;
  name: string;
  value_type: "text" | "number";
  value_text: string | null;
  value_number: number | null;
  unit: string | null;
  qualifier: "exact" | "min" | "max" | "approx" | null;
  unknown: boolean;
  display_value: string | null;
}

export interface RequirementItem extends Evidence {
  id: string;
  run_id: string;
  statement: string;
  category: string;
  priority: string;
  parameters: ParameterItem[];
}

export interface RunCounts {
  requirements: number;
  parameters: number;
  unknown: number;
  unresolved: number;
  verified: number;
  corrected: number;
  rejected: number;
}

export type RunStatus = "extracting" | "pending_review" | "ready_for_approval" | "approved" | "rejected" | "failed" | "superseded";

export interface ExtractionRun {
  id: string;
  document_id: string;
  filename: string | null;
  status: RunStatus;
  provider: string | null;
  model: string | null;
  flags: { code: string; [k: string]: unknown }[];
  error: string | null;
  created_by: string | null;
  approved_by: string | null;
  approved_at: string | null;
  decision_note: string | null;
  created_at: string;
  completed_at: string | null;
  counts: RunCounts;
  source_changed: boolean;
}

export interface RunDetail extends ExtractionRun {
  requirements: RequirementItem[];
  unknown_parameters: ParameterItem[];
}

export interface ReviewEvent {
  id: number;
  item_type: string;
  item_id: string;
  action: string;
  actor_email: string | null;
  from_state: string | null;
  to_state: string | null;
  note: string | null;
  created_at: string;
}

export interface QueueItem {
  item_type: "parameter" | "requirement";
  id: string;
  run_id: string;
  document_id: string;
  filename: string;
  label: string;
  value: string | null;
  requirement_statement: string | null;
  confidence: number;
  confidence_bucket: "high" | "medium" | "low";
  flags: string[];
  validation_state: ValidationState;
  grounding_status: string;
  source_quote: string | null;
}

// --- Suppliers ----------------------------------------------------------------------------------------------
export type SupplierStatus = "prospect" | "under_review" | "qualified" | "disqualified" | "blocked";

export interface SupplierCategory {
  id: string;
  name: string;
  slug: string;
  parent_id: string | null;
  description: string | null;
  supplier_count: number;
}

export interface SupplierContact {
  id: string;
  name: string;
  email: string;
  phone: string | null;
  role: string | null;
  is_primary: boolean;
}

export interface QualificationSummary {
  score: number;
  ready: boolean;
  missing_required: string[];
  expired: string[];
  qualified: boolean;
}

export interface SupplierListItem {
  id: string;
  code: string;
  name: string;
  status: SupplierStatus;
  country: string | null;
  categories: string[];
  contact_count: number;
  qualification: QualificationSummary;
}

export interface Supplier {
  id: string;
  code: string;
  name: string;
  legal_name: string | null;
  status: SupplierStatus;
  country: string | null;
  website: string | null;
  tax_id: string | null;
  address: string | null;
  notes: string | null;
  payment_terms: string | null;
  currency: string | null;
  typical_lead_time_days: number | null;
  tags: string[];
  categories: SupplierCategory[];
  contacts: SupplierContact[];
  qualification: QualificationSummary;
  submitted_by: string | null;
  decided_by: string | null;
  decided_at: string | null;
  status_note: string | null;
  created_at: string;
}

export interface Criterion {
  id: string;
  key: string;
  name: string;
  description: string | null;
  required: boolean;
  weight: number;
  active: boolean;
}

export interface QualificationCheck {
  criterion: Criterion;
  status: "pending" | "passed" | "failed" | "waived";
  effective_status: "pending" | "passed" | "failed" | "waived" | "expired";
  note: string | null;
  expires_at: string | null;
  checked_by: string | null;
  checked_at: string | null;
}

export interface Qualification {
  supplier_id: string;
  status: SupplierStatus;
  summary: QualificationSummary;
  checks: QualificationCheck[];
}

export interface DomainEvent {
  id: number;
  action: string;
  actor_email: string | null;
  from_state: string | null;
  to_state: string | null;
  data: Record<string, unknown> | null;
  note: string | null;
  created_at: string;
}

// --- RFQs ----------------------------------------------------------------------------------------------------
export type RfqStatus = "draft" | "pending_approval" | "approved" | "sent" | "closed" | "awarded" | "cancelled";

export interface RfqParam {
  name: string;
  value: string | number | null;
  unit: string | null;
  qualifier: string | null;
  unknown: boolean;
}

export interface RfqItem {
  id: string;
  position: number;
  line: number;
  kind: string;
  statement: string;
  category: string;
  priority: string;
  quantity: number | null;
  unit: string | null;
  parameters: RfqParam[];
  source_requirement_id: string | null;
}

export interface RfqSupplierLink {
  supplier_id: string;
  code: string;
  name: string;
  supplier_status: SupplierStatus;
  qualified: boolean;
  contact_count: number;
  status: "invited" | "sending" | "sent" | "failed" | "quoted" | "declined";
  sent_at: string | null;
}

export interface RfqListItem {
  id: string;
  number: string;
  title: string;
  status: RfqStatus;
  version: number;
  currency: string;
  response_due_date: string | null;
  supplier_count: number;
  quote_count: number;
  created_at: string;
}

export interface Rfq {
  id: string;
  number: string;
  title: string;
  status: RfqStatus;
  version: number;
  currency: string;
  response_due_date: string | null;
  message: string | null;
  category_id: string | null;
  category_name: string | null;
  source_run_id: string | null;
  content_sha256: string | null;
  created_by: string | null;
  submitted_by: string | null;
  approved_by: string | null;
  approved_at: string | null;
  sent_at: string | null;
  decision_note: string | null;
  awarded_supplier_id: string | null;
  award_comparison_id: string | null;
  awarded_at: string | null;
  award_note: string | null;
  award_recommendation_id?: string | null;
  required_by?: string | null;
  requested_incoterms?: string | null;
  requested_payment_terms?: string | null;
  created_at: string;
  items: RfqItem[];
  suppliers: RfqSupplierLink[];
  readiness: string[];
}

export interface RfqPackage {
  id: string;
  supplier_id: string;
  supplier_name: string;
  rfq_version: number;
  subject: string;
  to_addresses: string[];
  sha256: string;
  size_bytes: number;
  send_status: "not_sent" | "queued" | "sent" | "failed";
  sent_at: string | null;
  send_error: string | null;
  created_at: string;
}

export interface QuoteLine {
  id: string;
  position: number;
  rfq_item_id: string | null;
  match_status: "unmatched" | "suggested" | "confirmed";
  match_score: number;
  description: string;
  quantity: number | null;
  unit: string | null;
  unit_price: number | null;
  total_price: number | null;
  lead_time_days: number | null;
  moq?: number | null;
  confidence: number;
  source_quote: string | null;
}

export interface Quote {
  id: string;
  supplier_id: string;
  supplier_name: string;
  document_id: string | null;
  source: "analysis" | "manual" | "import";
  status: "draft" | "verified";
  currency: string | null;
  incoterms: string | null;
  payment_terms: string | null;
  valid_until: string | null;
  notes: string | null;
  verified_at: string | null;
  lines: QuoteLine[];
}

export interface CriterionScore {
  score: number | null;
  value: unknown;
}

export interface RankingRow {
  rank: number;
  supplier_id: string;
  supplier_name: string;
  supplier_code: string;
  score: number;
  criteria: { price: CriterionScore; lead_time: CriterionScore; coverage: CriterionScore; qualification: CriterionScore; risk?: CriterionScore };
  basket_total: number | null;
  lead_time_days: number | null;
  coverage: number;
  qualified: boolean;
  incoterms: string | null;
  payment_terms: string | null;
  valid_until: string | null;
  flags: string[];
  explanation: string[];
  // engine 2.0 and later; older stored comparisons do not have these
  currency?: string;
  risk?: Risk;
  deviations?: Deviation[];
  on_time?: boolean | null;
  slack_days?: number | null;
  moq_excess_total?: number | null;
}

export type Severity = "critical" | "major" | "minor" | "info";

export interface Deviation {
  code: string;
  severity: Severity;
  line: number | null;
  message: string;
}

export interface Risk {
  score: number;
  level: "low" | "medium" | "high";
  counts: Record<Severity, number>;
  factors: string[];
}

export interface ComparisonCell {
  unit_price: number | null;
  comparable_total: number | null;
  lead_time_days: number | null;
  flags: string[];
  requested_total?: number | null;
  moq?: number | null;
  moq_excess?: number | null;
  payable_quantity?: number | null;
  conversions?: string[];
  fx?: string | null;
}

export interface ComparisonResult {
  currency: string;
  as_of: string;
  weights: Record<string, number>;
  active_weights: Record<string, number>;
  basket_item_ids: string[];
  items: { item_id: string; line: number; statement: string; quantity: number | null; unit: string | null; cells: Record<string, ComparisonCell> }[];
  ranking: RankingRow[];
  excluded: { supplier_id: string; supplier_name: string; reason: string; detail: string }[];
  global_flags: string[];
  highest_score_supplier_id: string | null;
  decision_note: string;
  engine_version?: string;
  lead_time_analysis?: {
    required_by: string | null;
    available_days: number | null;
    fastest: number | null;
    median: number | null;
    slowest: number | null;
    late_suppliers: string[];
  };
  moq_analysis?: { suppliers_with_excess: { supplier_name: string; excess: number }[] };
  fx_used?: Record<string, { rate: number; valid_from: string; path: string; age_days: number }>;
}

export interface ComparisonRecord {
  id: string;
  created_at: string;
  params: Record<string, unknown>;
  inputs_sha256: string;
  stale: boolean;
  result: ComparisonResult;
}

export interface ComparisonBrief {
  id: string;
  created_at: string;
  weights: Record<string, number>;
  highest_score_supplier_id: string | null;
  stale: boolean;
}

export interface Recommendation {
  id: string;
  rfq_id: string;
  comparison_id: string;
  supplier_id: string;
  supplier_name: string;
  rationale: string;
  status: "pending" | "approved" | "rejected" | "withdrawn";
  snapshot: {
    rank?: number;
    score?: number;
    basket_total?: number | null;
    currency?: string;
    lead_time_days?: number | null;
    risk?: { score: number; level: string };
    deviations?: Deviation[];
    not_top_ranked?: boolean;
  };
  submitted_by: string | null;
  submitted_by_email: string | null;
  submitted_at: string;
  decided_by: string | null;
  decided_by_email: string | null;
  decided_at: string | null;
  decision_note: string | null;
  comparison_stale: boolean;
  not_top_ranked: boolean;
}

export interface FxRate {
  id: string;
  base: string;
  quote: string;
  rate: string;
  valid_from: string;
  source: string | null;
  created_at: string;
}

export interface UnitRule {
  id: string;
  from_unit: string;
  to_unit: string;
  factor: string;
  note: string | null;
  created_at: string;
}

export interface UnitInfo {
  unit: string;
  dimension: string;
  to_base: string;
  aliases: string[];
}

export interface QuoteImportResult {
  quote: Quote;
  warnings: string[];
  rows_read: number;
  rows_skipped: number;
  columns: Record<string, string>;
  currency_detected: string | null;
}

// --- Sourcing assistant ---------------------------------------------------------------------------------------
export interface AgentSource {
  id: string;
  kind: "rfq" | "supplier" | "document" | "comparison" | "analysis" | "quote" | "glossary" | "method" | "report" | "requirements" | "queue";
  title: string;
  path: string | null;
  ref: string | null;
  as_of: string | null;
  excerpt: string;
  sha256: string;
}

export interface AgentBlock {
  type: "text" | "heading";
  text: string;
  sources: string[];
}

export interface AgentRunBrief {
  id: string;
  mode: "deterministic" | "llm";
  status: "answered" | "fallback" | "refused" | "clarify" | "error";
  intent: string | null;
  provider: string | null;
  model: string | null;
  prompt_version: string;
  latency_ms: number;
  tools: { tool: string; status: string; ms: number; error?: string | null }[];
  flags: string[];
  model_output_rejected: string | null;
}

export interface AgentMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  blocks: AgentBlock[];
  unknowns: string[];
  follow_ups: string[];
  sources: AgentSource[];
  run: AgentRunBrief | null;
  feedback: number | null;
  created_at: string;
}

export interface AgentConversation {
  id: string;
  title: string;
  archived: boolean;
  context: Record<string, string>;
  created_at: string;
  updated_at: string;
}

export interface AgentConversationDetail extends AgentConversation {
  messages: AgentMessage[];
}

export interface AgentTurn {
  conversation: AgentConversation;
  user: AgentMessage;
  assistant: AgentMessage;
}

export interface AgentStatus {
  mode: "deterministic" | "llm";
  configured_mode: string;
  providers: string[];
  prompt_version: string;
  prompt_sha256: string;
  glossary_version: string;
  tools: { name: string; description: string }[];
  indexed_documents: number;
  unindexed_documents: number;
}

export interface AgentRun {
  id: string;
  conversation_id: string;
  user_id: string;
  user_email: string | null;
  question: string;
  mode: string;
  status: string;
  intent: string | null;
  prompt_version: string;
  prompt_sha256: string;
  provider: string | null;
  model: string | null;
  plan: { tool: string; args: Record<string, unknown>; by: string }[];
  tool_calls: { tool: string; args: Record<string, unknown>; status: string; ms: number; error?: string; sources?: string[]; summary?: Record<string, unknown> }[];
  llm_calls: { purpose: string; provider: string; model: string; status: string; ms: number; input_tokens?: number; output_tokens?: number; error?: string; prompt_sha256: string; response_sha256?: string; redacted: boolean }[];
  safety: { flags?: string[]; problems?: string[]; model_output_rejected?: string; reports?: string[] };
  source_ids: string[];
  answer_sha256: string | null;
  latency_ms: number;
  input_tokens: number | null;
  output_tokens: number | null;
  error: string | null;
  created_at: string;
  answer: AgentMessage | null;
}

export interface AgentReport {
  id: string;
  kind: string;
  title: string;
  subject_id: string | null;
  run_id: string | null;
  conversation_id: string | null;
  blocks: AgentBlock[];
  unknowns: string[];
  sources: AgentSource[];
  content_sha256: string;
  created_by: string | null;
  created_at: string;
}
