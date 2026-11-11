"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { Rfq, RfqPackage } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const SEND_VARIANT = { not_sent: "neutral", queued: "caution", sent: "default", failed: "destructive" } as const;

export function Packages({ rfq, canManage }: { rfq: Rfq; canManage: boolean }) {
  const packages = useFetch<RfqPackage[]>(`rfqs/${rfq.id}/packages`);
  const [error, setError] = useState<string | null>(null);
  const canGenerate = canManage && ["approved", "sent", "closed"].includes(rfq.status);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      packages.reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="space-y-3">
      {error && <p className="text-sm text-destructive">{error}</p>}
      <p className="text-sm text-muted-foreground">
        One complete email per supplier (PDF and JSON attached), stored exactly as approved. Sending re-checks its fingerprint, so a changed package is never sent.
      </p>
      <ul className="divide-y rounded-md border">
        {packages.data?.map((p) => (
          <li key={p.id} className="space-y-1 px-3 py-2 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium">{p.supplier_name} <span className="text-xs text-muted-foreground">v{p.rfq_version}</span></span>
              <span className="flex items-center gap-2">
                <Badge variant={SEND_VARIANT[p.send_status]}>{p.send_status.replace("_", " ")}</Badge>
                <Button asChild size="sm" variant="ghost"><a href={`/api/rfqs/${rfq.id}/packages/${p.id}/download`}>Download .eml</a></Button>
                {canManage && p.send_status === "failed" && p.rfq_version === rfq.version && (
                  <Button size="sm" variant="outline" onClick={() => act(() => api(`rfqs/${rfq.id}/packages/${p.id}/resend`, { method: "POST" }))}>Re-send</Button>
                )}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              To {p.to_addresses.join(", ")} · {(p.size_bytes / 1024).toFixed(0)} KB · sha256 {p.sha256.slice(0, 12)}…
              {p.sent_at && ` · sent ${formatDate(p.sent_at)}`}
            </p>
            {p.send_error && <p className="text-xs text-destructive">{p.send_error}</p>}
          </li>
        ))}
        {packages.data?.length === 0 && <li className="px-3 py-6 text-center text-sm text-muted-foreground">No packages yet.</li>}
      </ul>
      {canGenerate && (
        <Button variant="outline" size="sm" onClick={() => act(() => api(`rfqs/${rfq.id}/packages`, { method: "POST" }))}>
          Generate packages for manual sending
        </Button>
      )}
    </div>
  );
}
