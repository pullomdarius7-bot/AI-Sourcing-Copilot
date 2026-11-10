"use client";

import Link from "next/link";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { Rfq, SupplierListItem } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const LINK_VARIANT = { invited: "neutral", sending: "caution", sent: "default", failed: "destructive", quoted: "default", declined: "neutral" } as const;

export function RfqSuppliers({ rfq, editable, onChanged }: { rfq: Rfq; editable: boolean; onChanged: () => void }) {
  const suggestions = useFetch<{ supplier_id: string; name: string; code: string; qualification_score: number }[]>(editable ? `rfqs/${rfq.id}/supplier-suggestions` : null);
  const qualified = useFetch<{ items: SupplierListItem[] }>(editable ? "suppliers?status=qualified&limit=100" : null);
  const [pick, setPick] = useState("");
  const [error, setError] = useState<string | null>(null);
  const invitedIds = new Set(rfq.suppliers.map((s) => s.supplier_id));
  const options = (qualified.data?.items ?? []).filter((s) => !invitedIds.has(s.id) && s.qualification.qualified);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      onChanged();
      suggestions.reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="space-y-3">
      {error && <p className="text-sm text-destructive">{error}</p>}
      <ul className="divide-y rounded-md border">
        {rfq.suppliers.map((s) => (
          <li key={s.supplier_id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
            <span>
              <Link href={`/suppliers/${s.supplier_id}`} className="font-medium text-primary hover:underline">{s.name}</Link>{" "}
              <span className="font-mono text-xs text-muted-foreground">{s.code}</span>
              {!s.qualified && <Badge variant="destructive" className="ml-2">not qualified</Badge>}
              {s.contact_count === 0 && <Badge variant="destructive" className="ml-2">no contact</Badge>}
            </span>
            <span className="flex items-center gap-2">
              <Badge variant={LINK_VARIANT[s.status]}>{s.status}</Badge>
              {s.sent_at && <span className="text-xs text-muted-foreground">{formatDate(s.sent_at)}</span>}
              {editable && <Button size="sm" variant="ghost" onClick={() => act(() => api(`rfqs/${rfq.id}/suppliers/${s.supplier_id}`, { method: "DELETE" }))}>Remove</Button>}
            </span>
          </li>
        ))}
        {rfq.suppliers.length === 0 && <li className="px-3 py-6 text-center text-sm text-muted-foreground">No suppliers invited yet.</li>}
      </ul>
      {editable && (
        <div className="space-y-3">
          {suggestions.data && suggestions.data.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">Qualified suppliers in this RFQ&rsquo;s category</p>
              <div className="flex flex-wrap gap-2">
                {suggestions.data.map((s) => (
                  <Button key={s.supplier_id} size="sm" variant="outline" onClick={() => act(() => api(`rfqs/${rfq.id}/suppliers`, { body: { supplier_ids: [s.supplier_id] } }))}>
                    + {s.name} <span className="text-muted-foreground">{s.qualification_score}%</span>
                  </Button>
                ))}
              </div>
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <Select aria-label="Qualified supplier" className="max-w-sm" value={pick} onChange={(e) => setPick(e.target.value)}>
              <option value="">Choose a qualified supplier…</option>
              {options.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.code})</option>)}
            </Select>
            <Button variant="outline" disabled={!pick} onClick={() => act(async () => {
              await api(`rfqs/${rfq.id}/suppliers`, { body: { supplier_ids: [pick] } });
              setPick("");
            })}>Invite</Button>
          </div>
        </div>
      )}
    </div>
  );
}
