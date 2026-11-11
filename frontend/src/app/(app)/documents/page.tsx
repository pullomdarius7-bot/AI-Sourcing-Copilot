"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { AnalysisBadge, StatusBadge } from "@/components/status-badge";
import { useMe } from "@/components/me-context";
import { useFetch } from "@/lib/api";
import { ACTIVE_STATUSES, formatBytes, STATUS_LABEL } from "@/lib/docs";
import type { DocumentItem } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const PAGE = 25;

export default function DocumentsPage() {
  const me = useMe();
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(query);
      setOffset(0);
    }, 300);
    return () => clearTimeout(t);
  }, [query]);

  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (status) params.set("status", status);
  if (search) params.set("q", search);
  const { data, error, reload } = useFetch<{ items: DocumentItem[]; total: number }>(`documents?${params}`);

  const inProgress = data?.items.some((d) => ACTIVE_STATUSES.includes(d.status));
  useEffect(() => {
    if (!inProgress) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [inProgress, reload]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Documents</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {data ? `${data.total} document${data.total === 1 ? "" : "s"}` : "Loading…"}
            {inProgress && " · updating live"}
          </p>
        </div>
        {me.permissions.includes("documents:upload") && (
          <Button asChild>
            <Link href="/upload">Upload documents</Link>
          </Button>
        )}
      </div>

      <div className="flex flex-wrap gap-3">
        <Input
          aria-label="Search by file name"
          placeholder="Search by file name"
          className="max-w-xs"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          aria-label="Filter by status"
          className="h-10 rounded-md border border-input bg-card px-3 text-sm"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">All statuses</option>
          {Object.entries(STATUS_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      {error && <Alert variant="error">{error.message}</Alert>}

      <Card>
        <CardContent className="overflow-x-auto pt-5">
          <table className="w-full text-left text-sm">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-2 pr-4 font-medium">File</th>
                <th className="py-2 pr-4 font-medium">Status</th>
                <th className="py-2 pr-4 font-medium">AI analysis</th>
                <th className="py-2 pr-4 font-medium">Size</th>
                <th className="py-2 font-medium">Uploaded</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data?.items.map((d) => (
                <tr key={d.id}>
                  <td className="py-3 pr-4">
                    <Link href={`/documents/${d.id}`} className="font-medium text-primary hover:underline">
                      {d.filename}
                    </Link>
                  </td>
                  <td className="py-3 pr-4">
                    <StatusBadge status={d.status} />
                  </td>
                  <td className="py-3 pr-4">{d.status === "quarantined" ? "—" : <AnalysisBadge status={d.analysis_status} />}</td>
                  <td className="py-3 pr-4 text-muted-foreground">{formatBytes(d.size_bytes)}</td>
                  <td className="whitespace-nowrap py-3 text-muted-foreground">{formatDate(d.created_at)}</td>
                </tr>
              ))}
              {data && data.items.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-muted-foreground">
                    No documents match.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
          Previous
        </Button>
        <Button variant="outline" size="sm" disabled={!data || offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>
          Next
        </Button>
      </div>
    </div>
  );
}
