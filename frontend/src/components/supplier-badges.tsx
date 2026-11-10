import { Badge } from "@/components/ui/badge";
import type { RfqStatus, SupplierStatus } from "@/lib/types";

const SUPPLIER: Record<SupplierStatus, [string, "default" | "neutral" | "caution" | "destructive"]> = {
  prospect: ["Prospect", "neutral"],
  under_review: ["Under review", "caution"],
  qualified: ["Qualified", "default"],
  disqualified: ["Disqualified", "destructive"],
  blocked: ["Blocked", "destructive"],
};

export function SupplierStatusBadge({ status, qualified }: { status: SupplierStatus; qualified?: boolean }) {
  const [label, variant] = SUPPLIER[status];
  if (status === "qualified" && qualified === false) {
    return <Badge variant="caution" title="A required check is missing or has expired">Qualified · checks lapsed</Badge>;
  }
  return <Badge variant={variant}>{label}</Badge>;
}

const RFQ: Record<RfqStatus, [string, "default" | "neutral" | "caution" | "destructive"]> = {
  draft: ["Draft", "neutral"],
  pending_approval: ["Awaiting approval", "caution"],
  approved: ["Approved", "default"],
  sent: ["Out for quotation", "default"],
  closed: ["Closed", "neutral"],
  awarded: ["Awarded", "default"],
  cancelled: ["Cancelled", "destructive"],
};

export function RfqStatusBadge({ status }: { status: RfqStatus }) {
  const [label, variant] = RFQ[status];
  return <Badge variant={variant}>{label}</Badge>;
}

export const RFQ_STATUS_LABEL = Object.fromEntries(Object.entries(RFQ).map(([k, [v]]) => [k, v])) as Record<RfqStatus, string>;
export const SUPPLIER_STATUS_LABEL = Object.fromEntries(Object.entries(SUPPLIER).map(([k, [v]]) => [k, v])) as Record<SupplierStatus, string>;
