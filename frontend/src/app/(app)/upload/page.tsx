"use client";

import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import { Upload } from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useMe } from "@/components/me-context";
import { useFetch } from "@/lib/api";
import { formatBytes } from "@/lib/docs";
import type { DocumentItem } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Limits {
  max_upload_mb: number;
  extensions: string[];
}

interface Entry {
  key: string;
  name: string;
  size: number;
  state: "waiting" | "uploading" | "done" | "duplicate" | "error";
  progress: number;
  documentId?: string;
  error?: string;
}

function sendFile(file: File, onProgress: (pct: number) => void) {
  return new Promise<{ status: number; body: { document?: DocumentItem; duplicate?: boolean; error?: { message: string } } }>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/documents");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      try {
        resolve({ status: xhr.status, body: JSON.parse(xhr.responseText || "{}") });
      } catch {
        reject(new Error("Unexpected response from the server"));
      }
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}

export default function UploadPage() {
  const me = useMe();
  const { data: limits } = useFetch<Limits>("documents/limits");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const busy = useRef(false);

  const patch = useCallback((key: string, change: Partial<Entry>) => {
    setEntries((list) => list.map((e) => (e.key === key ? { ...e, ...change } : e)));
  }, []);

  const handleFiles = useCallback(
    async (files: FileList | File[]) => {
      if (!limits || busy.current) return;
      busy.current = true;
      const batch = Array.from(files).map((f, i) => ({ file: f, key: `${Date.now()}-${i}-${f.name}` }));
      setEntries((list) => [
        ...batch.map(({ file, key }): Entry => ({ key, name: file.name, size: file.size, state: "waiting", progress: 0 })),
        ...list,
      ]);
      for (const { file, key } of batch) {
        const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
        if (!limits.extensions.includes(ext)) {
          patch(key, { state: "error", error: `Unsupported type. Allowed: ${limits.extensions.join(", ")}` });
          continue;
        }
        if (file.size > limits.max_upload_mb * 1024 * 1024) {
          patch(key, { state: "error", error: `Larger than the ${limits.max_upload_mb} MB limit` });
          continue;
        }
        patch(key, { state: "uploading" });
        try {
          const { status, body } = await sendFile(file, (progress) => patch(key, { progress }));
          if (status === 201 || status === 200) {
            patch(key, { state: body.duplicate ? "duplicate" : "done", progress: 100, documentId: body.document?.id });
          } else {
            patch(key, { state: "error", error: body.error?.message ?? `Upload failed (${status})` });
          }
        } catch (err) {
          patch(key, { state: "error", error: err instanceof Error ? err.message : "Upload failed" });
        }
      }
      busy.current = false;
    },
    [limits, patch],
  );

  if (!me.permissions.includes("documents:upload")) {
    return <Alert variant="error">Your role cannot upload documents.</Alert>;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Upload documents</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Every file is checked for malware, parsed and, when an AI provider is configured, analyzed for supplier terms.
        </p>
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void handleFiles(e.dataTransfer.files);
        }}
        className={cn(
          "flex flex-col items-center gap-3 rounded-lg border-2 border-dashed bg-card px-6 py-12 text-center transition-colors",
          dragging ? "border-primary bg-primary/5" : "border-input",
        )}
      >
        <Upload className="h-8 w-8 text-muted-foreground" aria-hidden />
        <p className="text-sm">Drag files here, or</p>
        <Button onClick={() => inputRef.current?.click()} disabled={!limits}>
          Choose files
        </Button>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="sr-only"
          aria-label="Choose files to upload"
          accept={limits?.extensions.join(",")}
          onChange={(e) => {
            if (e.target.files) void handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
        {limits && (
          <p className="text-xs text-muted-foreground">
            {limits.extensions.join("  ")} · up to {limits.max_upload_mb} MB each
          </p>
        )}
      </div>

      {entries.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>This session</CardTitle>
            <CardDescription>Identical files already in your library are detected and not stored twice.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {entries.map((e) => (
                <li key={e.key} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{e.name}</p>
                    <p className="text-xs text-muted-foreground">{formatBytes(e.size)}</p>
                    {e.error && <p className="text-xs text-destructive">{e.error}</p>}
                  </div>
                  <div className="flex items-center gap-3">
                    {e.state === "uploading" && (
                      <div className="h-2 w-32 overflow-hidden rounded-full bg-secondary" role="progressbar" aria-valuenow={e.progress} aria-valuemin={0} aria-valuemax={100}>
                        <div className="h-full bg-primary transition-all" style={{ width: `${e.progress}%` }} />
                      </div>
                    )}
                    {e.state === "waiting" && <Badge>Waiting</Badge>}
                    {e.state === "done" && <Badge variant="default">Uploaded</Badge>}
                    {e.state === "duplicate" && <Badge variant="caution">Already in your library</Badge>}
                    {e.state === "error" && <Badge variant="destructive">Rejected</Badge>}
                    {e.documentId && (
                      <Link href={`/documents/${e.documentId}`} className="text-primary hover:underline">
                        Track
                      </Link>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
