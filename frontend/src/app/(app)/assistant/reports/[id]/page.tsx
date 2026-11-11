"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Blocks, Sources, Unknowns } from "@/components/assistant/answer";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useFetch } from "@/lib/api";
import type { AgentReport } from "@/lib/types";
import { formatDate } from "@/lib/utils";

export default function ReportPage() {
  const { id } = useParams<{ id: string }>();
  const report = useFetch<AgentReport>(`agent/reports/${id}`);
  const [active, setActive] = useState<string | null>(null);
  if (report.error) return <Alert variant="error">{report.error.message}</Alert>;
  const r = report.data;
  if (!r) return <p className="text-sm text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs text-muted-foreground"><Link href="/assistant" className="hover:underline">Assistant</Link> / Report</p>
          <h1 className="text-2xl font-semibold tracking-tight">{r.title}</h1>
          <p className="text-sm text-muted-foreground">
            Generated {formatDate(r.created_at)} · {r.sources.length} sources · fingerprint <span className="font-mono">{r.content_sha256.slice(0, 16)}</span>
            {r.run_id && <> · <Link href={`/assistant/runs/${r.run_id}`} className="hover:underline">trace</Link></>}
          </p>
        </div>
        <div className="flex gap-2">
          <Button asChild variant="outline"><a href={`/api/agent/reports/${r.id}/pdf`}>PDF</a></Button>
          <Button asChild variant="outline"><a href={`/api/agent/reports/${r.id}/markdown`}>Markdown</a></Button>
        </div>
      </div>
      <Card>
        <CardContent className="pt-6"><Blocks blocks={r.blocks} active={active} onCite={setActive} /></CardContent>
      </Card>
      <Unknowns items={r.unknowns} />
      <Card>
        <CardHeader>
          <CardTitle>Sources</CardTitle>
          <CardDescription>The report is a stored snapshot; the linked pages show the current state.</CardDescription>
        </CardHeader>
        <CardContent><Sources sources={r.sources} active={active} onSelect={setActive} /></CardContent>
      </Card>
    </div>
  );
}
