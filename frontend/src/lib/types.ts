export type Role = "requester" | "buyer" | "approver" | "admin" | "auditor";
export const ROLES: Role[] = ["requester", "buyer", "approver", "admin", "auditor"];

export interface Me {
  id: string;
  organization_id: string;
  organization_name: string;
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
