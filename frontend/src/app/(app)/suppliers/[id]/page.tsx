"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { History } from "@/components/history";
import { SupplierStatusBadge } from "@/components/supplier-badges";
import { useMe } from "@/components/me-context";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { DomainEvent, Qualification, QualificationCheck, Supplier, SupplierCategory } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const CHECK_VARIANT = { passed: "default", waived: "default", failed: "destructive", expired: "destructive", pending: "caution" } as const;

function Profile({ s, canEdit, onSaved }: { s: Supplier; canEdit: boolean; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    name: s.name, legal_name: s.legal_name ?? "", country: s.country ?? "", website: s.website ?? "", tax_id: s.tax_id ?? "",
    address: s.address ?? "", payment_terms: s.payment_terms ?? "", currency: s.currency ?? "",
    typical_lead_time_days: s.typical_lead_time_days?.toString() ?? "", tags: s.tags.join(", "), notes: s.notes ?? "",
  });
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setForm({ ...form, [k]: e.target.value });
  const rows: [string, React.ReactNode][] = [
    ["Legal name", s.legal_name], ["Country", s.country], ["Website", s.website], ["Tax ID", s.tax_id], ["Address", s.address],
    ["Payment terms", s.payment_terms], ["Currency", s.currency],
    ["Typical lead time", s.typical_lead_time_days != null ? `${s.typical_lead_time_days} days` : null],
    ["Tags", s.tags.length ? s.tags.join(", ") : null], ["Notes", s.notes],
  ];

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const blank = (v: string) => (v.trim() ? v.trim() : null);
    try {
      await api(`suppliers/${s.id}`, {
        method: "PATCH",
        body: {
          name: form.name, legal_name: blank(form.legal_name), country: blank(form.country), website: blank(form.website),
          tax_id: blank(form.tax_id), address: blank(form.address), payment_terms: blank(form.payment_terms),
          currency: blank(form.currency), notes: blank(form.notes),
          typical_lead_time_days: form.typical_lead_time_days ? Number(form.typical_lead_time_days) : null,
          tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
        },
      });
      setEditing(false);
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Profile</CardTitle>
          {canEdit && !editing && <Button size="sm" variant="outline" onClick={() => setEditing(true)}>Edit</Button>}
        </div>
      </CardHeader>
      <CardContent>
        {editing ? (
          <form onSubmit={save} className="grid gap-3 md:grid-cols-2">
            {error && <Alert variant="error" className="md:col-span-2">{error}</Alert>}
            {([
              ["name", "Company name"], ["legal_name", "Legal name"], ["country", "Country (2 letters)"], ["website", "Website"],
              ["tax_id", "Tax ID"], ["payment_terms", "Payment terms"], ["currency", "Currency (3 letters)"],
              ["typical_lead_time_days", "Typical lead time (days)"], ["tags", "Tags (comma separated)"],
            ] as const).map(([k, label]) => (
              <div key={k} className="space-y-1">
                <Label htmlFor={`p-${k}`}>{label}</Label>
                <Input id={`p-${k}`} value={form[k]} onChange={set(k)} type={k === "typical_lead_time_days" ? "number" : "text"} min={0} />
              </div>
            ))}
            <div className="space-y-1 md:col-span-2">
              <Label htmlFor="p-address">Address</Label>
              <Textarea id="p-address" value={form.address} onChange={set("address")} />
            </div>
            <div className="space-y-1 md:col-span-2">
              <Label htmlFor="p-notes">Notes</Label>
              <Textarea id="p-notes" value={form.notes} onChange={set("notes")} />
            </div>
            <div className="flex gap-2">
              <Button type="submit">Save</Button>
              <Button type="button" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
            </div>
          </form>
        ) : (
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            {rows.map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3 border-b py-1">
                <dt className="text-muted-foreground">{k}</dt>
                <dd className="text-right">{v ?? <span className="text-muted-foreground">—</span>}</dd>
              </div>
            ))}
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

function Categories({ s, canEdit, onSaved }: { s: Supplier; canEdit: boolean; onSaved: () => void }) {
  const all = useFetch<SupplierCategory[]>("supplier-categories");
  const [selected, setSelected] = useState<string[]>(s.categories.map((c) => c.id));
  const [error, setError] = useState<string | null>(null);
  const changed = selected.slice().sort().join() !== s.categories.map((c) => c.id).sort().join();
  return (
    <Card>
      <CardHeader>
        <CardTitle>Categories</CardTitle>
        <CardDescription>Used to suggest this supplier for RFQs in the same category.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && <Alert variant="error">{error}</Alert>}
        {canEdit ? (
          <>
            <div className="flex flex-wrap gap-3">
              {all.data?.map((c) => (
                <label key={c.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={selected.includes(c.id)} onChange={(e) => setSelected(e.target.checked ? [...selected, c.id] : selected.filter((x) => x !== c.id))} />
                  {c.name}
                </label>
              ))}
              {all.data?.length === 0 && <p className="text-sm text-muted-foreground">No categories yet. Create them on the Suppliers page.</p>}
            </div>
            {changed && (
              <Button size="sm" onClick={async () => {
                setError(null);
                try {
                  await api(`suppliers/${s.id}/categories`, { method: "PUT", body: { category_ids: selected } });
                  onSaved();
                } catch (err) {
                  setError(errorMessage(err));
                }
              }}>Save categories</Button>
            )}
          </>
        ) : (
          <div className="flex flex-wrap gap-1">{s.categories.map((c) => <Badge key={c.id}>{c.name}</Badge>)}</div>
        )}
      </CardContent>
    </Card>
  );
}

function Contacts({ s, canEdit, onSaved }: { s: Supplier; canEdit: boolean; onSaved: () => void }) {
  const [form, setForm] = useState({ name: "", email: "", phone: "", role: "" });
  const [error, setError] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Contacts</CardTitle>
        <CardDescription>RFQs go to the primary contact, with the others in copy.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <Alert variant="error">{error}</Alert>}
        <ul className="divide-y rounded-md border">
          {s.contacts.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
              <span>
                <span className="font-medium">{c.name}</span> <span className="text-muted-foreground">{c.email}</span>
                {c.role && <span className="text-muted-foreground"> · {c.role}</span>}
                {c.phone && <span className="text-muted-foreground"> · {c.phone}</span>}
                {c.is_primary && <Badge variant="default" className="ml-2">Primary</Badge>}
              </span>
              {canEdit && (
                <span className="flex gap-1">
                  {!c.is_primary && (
                    <Button size="sm" variant="ghost" onClick={() => act(() => api(`suppliers/${s.id}/contacts/${c.id}`, { method: "PATCH", body: { is_primary: true } }))}>Make primary</Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => act(() => api(`suppliers/${s.id}/contacts/${c.id}`, { method: "DELETE" }))}>Remove</Button>
                </span>
              )}
            </li>
          ))}
          {s.contacts.length === 0 && <li className="px-3 py-4 text-sm text-caution">No contacts. A supplier needs one before it can be qualified or receive an RFQ.</li>}
        </ul>
        {canEdit && (
          <form
            className="grid gap-2 md:grid-cols-5"
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                await api(`suppliers/${s.id}/contacts`, { body: { ...form, phone: form.phone || null, role: form.role || null } });
                setForm({ name: "", email: "", phone: "", role: "" });
              });
            }}
          >
            <Input aria-label="Name" placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
            <Input aria-label="Email" placeholder="Email" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
            <Input aria-label="Phone" placeholder="Phone" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
            <Input aria-label="Role" placeholder="Role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} />
            <Button type="submit" variant="outline">Add contact</Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

function CheckRow({ supplierId, check, canRecord, canWaive, onSaved }: {
  supplierId: string; check: QualificationCheck; canRecord: boolean; canWaive: boolean; onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState(check.status === "pending" ? "passed" : check.status);
  const [note, setNote] = useState(check.note ?? "");
  const [expires, setExpires] = useState(check.expires_at ?? "");
  const [error, setError] = useState<string | null>(null);
  return (
    <li className="space-y-2 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium">
            {check.criterion.name}
            {check.criterion.required && <Badge variant="neutral" className="ml-2">Required</Badge>}
          </p>
          <p className="text-xs text-muted-foreground">
            Weight {check.criterion.weight}
            {check.checked_at && ` · recorded ${formatDate(check.checked_at)}`}
            {check.expires_at && ` · valid until ${check.expires_at}`}
            {check.note && ` · “${check.note}”`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={CHECK_VARIANT[check.effective_status]}>{check.effective_status}</Badge>
          {canRecord && !open && <Button size="sm" variant="outline" onClick={() => setOpen(true)}>Record</Button>}
        </div>
      </div>
      {open && (
        <form
          className="grid gap-2 rounded-md bg-muted p-3 md:grid-cols-[10rem_1fr_10rem_auto]"
          onSubmit={async (e) => {
            e.preventDefault();
            setError(null);
            try {
              await api(`suppliers/${supplierId}/qualification/${check.criterion.id}`, {
                method: "PUT",
                body: { status, note: note || null, expires_at: status === "passed" && expires ? expires : null },
              });
              setOpen(false);
              onSaved();
            } catch (err) {
              setError(errorMessage(err));
            }
          }}
        >
          <Select aria-label="Result" value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
            <option value="passed">Passed</option>
            <option value="failed">Failed</option>
            <option value="pending">Pending</option>
            {canWaive && <option value="waived">Waived</option>}
          </Select>
          <Input aria-label="Note or evidence" placeholder={status === "waived" ? "Why is this waived? (required)" : "Note or evidence reference"} value={note} onChange={(e) => setNote(e.target.value)} />
          <Input aria-label="Valid until" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} disabled={status !== "passed"} />
          <div className="flex gap-1">
            <Button type="submit" size="sm">Save</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          </div>
          {error && <p className="text-sm text-destructive md:col-span-4">{error}</p>}
        </form>
      )}
    </li>
  );
}

export default function SupplierPage() {
  const { id } = useParams<{ id: string }>();
  const me = useMe();
  const supplier = useFetch<Supplier>(`suppliers/${id}`);
  const qualification = useFetch<Qualification>(`suppliers/${id}/qualification`);
  const events = useFetch<DomainEvent[]>(`suppliers/${id}/events`);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const canManage = me.permissions.includes("suppliers:manage");
  const canQualify = me.permissions.includes("suppliers:qualify");

  function reload() {
    supplier.reload();
    qualification.reload();
    events.reload();
  }

  async function act(path: string, body: unknown) {
    setError(null);
    try {
      await api(`suppliers/${id}/${path}`, { method: "POST", body });
      setReason("");
      reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  if (supplier.error) return <Alert variant="error">{supplier.error.message}</Alert>;
  const s = supplier.data;
  if (!s) return <p className="text-sm text-muted-foreground">Loading…</p>;
  const q = qualification.data;

  return (
    <div className="space-y-6">
      <div>
        <Link href="/suppliers" className="text-sm text-primary hover:underline">← All suppliers</Link>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold">{s.name}</h1>
            <p className="font-mono text-xs text-muted-foreground">{s.code}</p>
          </div>
          <SupplierStatusBadge status={s.status} qualified={s.qualification.qualified} />
        </div>
        {s.status_note && <p className="mt-2 text-sm text-muted-foreground">Status note: {s.status_note}</p>}
      </div>

      {error && <Alert variant="error">{error}</Alert>}
      {s.status === "qualified" && !s.qualification.qualified && (
        <Alert variant="error">
          Qualification has lapsed: {[...s.qualification.missing_required, ...s.qualification.expired].join(", ")}. The supplier cannot receive RFQs until this is fixed.
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Qualification</CardTitle>
          <CardDescription>
            {q ? `Checklist score ${q.summary.score}%. ` : ""}
            Required checks must be passed or waived before an approver can qualify the supplier. The approver cannot be the person who submitted it.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ul className="divide-y">
            {q?.checks.filter((c) => c.criterion.active).map((c) => (
              <CheckRow key={c.criterion.id} supplierId={s.id} check={c} canRecord={canManage && s.status !== "blocked"} canWaive={canQualify} onSaved={reload} />
            ))}
          </ul>
          <div className="flex flex-wrap items-end gap-2 border-t pt-4">
            {canManage && ["prospect", "disqualified"].includes(s.status) && (
              <Button onClick={() => act("submit", undefined)}>Submit for qualification</Button>
            )}
            {canQualify && s.status === "under_review" && (
              <Button onClick={() => act("qualify", { note: reason || null })} disabled={!q?.summary.ready}>Qualify</Button>
            )}
            {canQualify && (
              <>
                <Input aria-label="Reason" placeholder="Reason (required to disqualify or block)" className="max-w-xs" value={reason} onChange={(e) => setReason(e.target.value)} />
                {["under_review", "qualified"].includes(s.status) && (
                  <Button variant="outline" disabled={reason.trim().length < 3} onClick={() => act("disqualify", { reason })}>Disqualify</Button>
                )}
                {s.status !== "blocked" && (
                  <Button variant="destructive" disabled={reason.trim().length < 3} onClick={() => act("block", { reason })}>Block</Button>
                )}
                {["disqualified", "blocked"].includes(s.status) && (
                  <Button variant="outline" onClick={() => act("reopen", { note: reason || null })}>Reopen as prospect</Button>
                )}
              </>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Profile key={s.id + s.name} s={s} canEdit={canManage} onSaved={reload} />
        <div className="space-y-6">
          <Contacts s={s} canEdit={canManage} onSaved={reload} />
          <Categories key={s.categories.map((c) => c.id).join()} s={s} canEdit={canManage} onSaved={reload} />
        </div>
      </div>

      <Card>
        <CardHeader><CardTitle>History</CardTitle></CardHeader>
        <CardContent><History events={events.data ?? []} /></CardContent>
      </Card>
    </div>
  );
}
