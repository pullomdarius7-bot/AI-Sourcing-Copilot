import type { DocumentDetail, DocumentItem, JobBrief } from "@/lib/types";

export const ACTIVE_STATUSES = ["uploaded", "scanning", "extracting", "analyzing"];

export function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export const STATUS_LABEL: Record<DocumentItem["status"], string> = {
  uploaded: "Queued",
  scanning: "Scanning",
  extracting: "Extracting",
  analyzing: "Analyzing",
  processed: "Processed",
  quarantined: "Quarantined",
  failed: "Failed",
};

export function statusVariant(status: DocumentItem["status"]): "default" | "neutral" | "caution" | "destructive" {
  if (status === "processed") return "default";
  if (status === "quarantined" || status === "failed") return "destructive";
  return "caution";
}

export type StepState = "pending" | "queued" | "running" | "done" | "failed" | "skipped" | "review";

export interface Step {
  key: string;
  label: string;
  state: StepState;
  detail?: string;
}

function latest(jobs: JobBrief[], kind: string) {
  return [...jobs].filter((j) => j.kind === kind).sort((a, b) => a.run_at.localeCompare(b.run_at)).pop();
}

function fromJob(job: JobBrief | undefined): StepState {
  if (!job) return "pending";
  if (job.status === "queued") return "queued";
  if (job.status === "running") return "running";
  if (job.status === "succeeded") return "done";
  return "failed";
}

export function pipelineSteps(doc: DocumentDetail): Step[] {
  const scanJob = latest(doc.jobs, "document.scan");
  const extractJob = latest(doc.jobs, "document.extract");
  const analyzeJob = latest(doc.jobs, "document.analyze");

  let scan = fromJob(scanJob);
  let scanDetail = scanJob && scanJob.status === "queued" && scanJob.attempts > 0 ? `Retrying (attempt ${scanJob.attempts + 1} of ${scanJob.max_attempts})` : undefined;
  if (doc.scan_status === "infected") {
    scan = "failed";
    scanDetail = `Threat found: ${doc.scan_detail ?? "unknown"}`;
  } else if (doc.scan_status === "clean") {
    scan = "done";
    scanDetail = doc.scan_engine ? `Clean (${doc.scan_engine})` : "Clean";
  }

  const quarantined = doc.status === "quarantined";
  const extract: StepState = quarantined ? "skipped" : fromJob(extractJob);
  let analyze: StepState = quarantined ? "skipped" : fromJob(analyzeJob);
  let analyzeDetail: string | undefined;
  if (analyze === "done") {
    if (doc.analysis_status === "skipped") {
      analyze = "skipped";
      analyzeDetail = String(doc.file_metadata.analysis_skipped_reason ?? "Skipped");
    } else if (doc.analysis_status === "needs_review") {
      analyze = "review";
      analyzeDetail = "Needs human review";
    }
  }
  if (analyze === "failed" && doc.error) analyzeDetail = doc.error;
  const extractDetail = extract === "failed" ? doc.error ?? undefined : extract === "queued" && extractJob && extractJob.attempts > 0 ? `Retrying (attempt ${extractJob.attempts + 1} of ${extractJob.max_attempts})` : undefined;
  if (analyze === "queued" && analyzeJob && analyzeJob.attempts > 0) analyzeDetail = `Retrying (attempt ${analyzeJob.attempts + 1} of ${analyzeJob.max_attempts}): ${analyzeJob.last_error ?? ""}`;

  return [
    { key: "upload", label: "Uploaded", state: "done" },
    { key: "scan", label: "Virus scan", state: scan, detail: scanDetail },
    { key: "extract", label: "Text & metadata", state: extract, detail: extractDetail },
    { key: "analyze", label: "AI analysis", state: analyze, detail: analyzeDetail },
  ];
}
