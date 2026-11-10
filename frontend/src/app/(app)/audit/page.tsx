"use client";

import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useFetch } from "@/lib/api";
import type { AuditEntry } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import { useMe } from "@/components/me-context";

const PAGE = 50;

export default function AuditPage() {
  const me = useMe();
  const allowed = me.permissions.includes("audit:read");
  const [offset, setOffset] = useState(0);
  const { data, error, loading } = useFetch<AuditEntry[]>(allowed ? `audit-logs?limit=${PAGE}&offset=${offset}` : null);

  if (!allowed) return <Alert variant="error">Your role cannot view the audit log.</Alert>;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Audit log</h1>
        <p className="mt-1 text-sm text-muted-foreground">Security and administration events for {me.organization_name}, newest first.</p>
      </div>
      {error && <Alert variant="error">{error.message}</Alert>}
      <Card>
        <CardContent className="overflow-x-auto pt-5">
          <table className="w-full text-left text-sm">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-2 pr-4 font-medium">When</th>
                <th className="py-2 pr-4 font-medium">Who</th>
                <th className="py-2 pr-4 font-medium">What</th>
                <th className="py-2 font-medium">From</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data?.map((row) => (
                <tr key={row.id}>
                  <td className="whitespace-nowrap py-2 pr-4 text-muted-foreground">{formatDate(row.created_at)}</td>
                  <td className="py-2 pr-4">{row.actor_email ?? "—"}</td>
                  <td className="py-2 pr-4 font-mono text-xs">{row.action}</td>
                  <td className="py-2 font-mono text-xs text-muted-foreground">{row.ip ?? "—"}</td>
                </tr>
              ))}
              {data && data.length === 0 && (
                <tr>
                  <td colSpan={4} className="py-6 text-center text-muted-foreground">
                    No events on this page.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
          Newer
        </Button>
        <Button variant="outline" size="sm" disabled={loading || (data?.length ?? 0) < PAGE} onClick={() => setOffset(offset + PAGE)}>
          Older
        </Button>
      </div>
    </div>
  );
}
