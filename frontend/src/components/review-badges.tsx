import { Badge } from "@/components/ui/badge";
import { confidenceText, STATE_LABEL } from "@/lib/evidence";
import type { RunStatus, ValidationState } from "@/lib/types";

export function ConfidenceBadge({ value, unknown }: { value: number; unknown?: boolean }) {
  if (unknown) return <Badge variant="neutral">no value</Badge>;
  const variant = value >= 0.85 ? "default" : value >= 0.6 ? "caution" : "destructive";
  return (
    <Badge variant={variant} title="Confidence after checking the quote against the document">
      {confidenceText(value)}
    </Badge>
  );
}

export function StateBadge({ state }: { state: ValidationState }) {
  const variant = state === "verified" || state === "corrected" ? "default" : state === "rejected" ? "destructive" : "caution";
  return <Badge variant={variant}>{STATE_LABEL[state]}</Badge>;
}

const RUN_LABEL: Record<RunStatus, string> = {
  extracting: "Extracting",
  pending_review: "In review",
  ready_for_approval: "Ready for approval",
  approved: "Approved",
  rejected: "Rejected",
  failed: "Failed",
  superseded: "Superseded",
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const variant =
    status === "approved" ? "default" : status === "failed" || status === "rejected" ? "destructive" : status === "superseded" ? "neutral" : "caution";
  return <Badge variant={variant}>{RUN_LABEL[status]}</Badge>;
}
