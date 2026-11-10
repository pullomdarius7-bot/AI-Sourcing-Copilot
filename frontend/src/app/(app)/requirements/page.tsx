"use client";

import Link from "next/link";
import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { RunStatusBadge } from "@/components/review-badges";
import { useFetch } from "@/lib/api";
import type { ExtractionRun } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const FILTERS = [
  ["", "All"],
  ["pending_review", "In review"],
  ["ready_for_approval", "Ready for approval"],
  ["approved", "Approved"],
  ["rejected", "Rejected"],
  ["failed", "Failed"],
] as const;

export default function RequirementsPage() {
  const [status, setStatus] = useState("");
  const { data, error } = useFetch<ExtractionRun[]>(`extraction-runs?limit=100${status ? `&status=${status}` : ""}`);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Requirements</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Extractions from your documents. Nothing is trusted until it has been checked against the source and approved.
          Start an extraction from a document&rsquo;s page.
        </p>
      </div>
      <select aria-label="Filter by status" className="h-10 rounded-md border border-input bg-card px-3 text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
        {FILTERS.map(([value, label]) => (
          <option key={value} value={value}>{label}</option>
        ))}
      </select>
      {error && <Alert variant="error">{error.message}</Alert>}
      <Card>
        <CardContent className="overflow-x-auto pt-5">
          <table className="w-full text-left text-sm">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-2 pr-4 font-medium">Document</th>
                <th className="py-2 pr-4 font-medium">Status</th>
                <th className="py-2 pr-4 font-medium">Requirements</th>
                <th className="py-2 pr-4 font-medium">Review progress</th>
                <th className="py-2 font-medium">Started</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data?.map((r) => {
                const total = r.counts.requirements + r.counts.parameters;
                const done = total - r.counts.unresolved;
                return (
                  <tr key={r.id}>
                    <td className="py-3 pr-4">
                      <Link href={`/requirements/${r.id}`} className="font-medium text-primary hover:underline">{r.filename ?? r.document_id}</Link>
                    </td>
                    <td className="py-3 pr-4"><RunStatusBadge status={r.status} /></td>
                    <td className="py-3 pr-4">{r.counts.requirements} <span className="text-muted-foreground">({r.counts.parameters} parameters, {r.counts.unknown} unknown)</span></td>
                    <td className="py-3 pr-4">
                      {total > 0 ? (
                        <div className="flex items-center gap-2">
                          <div className="h-2 w-24 overflow-hidden rounded-full bg-secondary" role="progressbar" aria-valuenow={done} aria-valuemin={0} aria-valuemax={total}>
                            <div className="h-full bg-primary" style={{ width: `${(done / total) * 100}%` }} />
                          </div>
                          <span className="text-xs text-muted-foreground">{done}/{total}</span>
                        </div>
                      ) : "—"}
                    </td>
                    <td className="whitespace-nowrap py-3 text-muted-foreground">{formatDate(r.created_at)}</td>
                  </tr>
                );
              })}
              {data && data.length === 0 && (
                <tr><td colSpan={5} className="py-8 text-center text-muted-foreground">No extractions yet.</td></tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
