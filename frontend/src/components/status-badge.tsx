import { Badge } from "@/components/ui/badge";
import { STATUS_LABEL, statusVariant } from "@/lib/docs";
import type { DocumentItem } from "@/lib/types";

export function StatusBadge({ status }: { status: DocumentItem["status"] }) {
  return <Badge variant={statusVariant(status)}>{STATUS_LABEL[status]}</Badge>;
}

export function AnalysisBadge({ status }: { status: DocumentItem["analysis_status"] }) {
  const map = {
    pending: ["neutral", "Pending"],
    succeeded: ["default", "Analyzed"],
    needs_review: ["caution", "Needs review"],
    skipped: ["neutral", "Skipped"],
    failed: ["destructive", "Failed"],
  } as const;
  const [variant, label] = map[status];
  return <Badge variant={variant}>{label}</Badge>;
}
