"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { SUPPLIER_STATUS_LABEL, SupplierStatusBadge } from "@/components/supplier-badges";
import { useMe } from "@/components/me-context";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { Supplier, SupplierCategory, SupplierListItem } from "@/lib/types";

const PAGE = 25;

function NewSupplier({ categories, onCreated }: { categories: SupplierCategory[]; onCreated: (id: string) => void }) {
  const [form, setForm] = useState({ name: "", country: "", contactName: "", contactEmail: "", categoryId: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const s = await api<Supplier>("suppliers", {
        body: {
          name: form.name,
          country: form.country || null,
          category_ids: form.categoryId ? [form.categoryId] : [],
          contacts: form.contactEmail ? [{ name: form.contactName || form.contactEmail, email: form.contactEmail, is_primary: true }] : [],
        },
      });
      onCreated(s.id);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
      {error && <Alert variant="error" className="md:col-span-2">{error}</Alert>}
      <div className="space-y-1">
        <Label htmlFor="s-name">Company name</Label>
        <Input id="s-name" value={form.name} onChange={set("name")} required minLength={2} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="s-country">Country (2 letters)</Label>
        <Input id="s-country" value={form.country} onChange={set("country")} maxLength={2} placeholder="DE" />
      </div>
      <div className="space-y-1">
        <Label htmlFor="s-cname">Contact name</Label>
        <Input id="s-cname" value={form.contactName} onChange={set("contactName")} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="s-cemail">Contact email</Label>
        <Input id="s-cemail" type="email" value={form.contactEmail} onChange={set("contactEmail")} />
      </div>
      <div className="space-y-1">
        <Label htmlFor="s-cat">Category</Label>
        <Select id="s-cat" value={form.categoryId} onChange={set("categoryId")}>
          <option value="">None</option>
          {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select>
      </div>
      <div className="flex items-end">
        <Button type="submit" disabled={busy}>{busy ? "Creating…" : "Create supplier"}</Button>
      </div>
    </form>
  );
}

function NewCategory({ categories, onCreated }: { categories: SupplierCategory[]; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [parent, setParent] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={async (e) => {
        e.preventDefault();
        setError(null);
        try {
          await api("supplier-categories", { body: { name, parent_id: parent || null } });
          setName("");
          setParent("");
          onCreated();
        } catch (err) {
          setError(errorMessage(err));
        }
      }}
    >
      <div className="min-w-[12rem] flex-1 space-y-1">
        <Label htmlFor="c-name">New category</Label>
        <Input id="c-name" value={name} onChange={(e) => setName(e.target.value)} required minLength={2} />
      </div>
      <div className="min-w-[12rem] space-y-1">
        <Label htmlFor="c-parent">Inside</Label>
        <Select id="c-parent" value={parent} onChange={(e) => setParent(e.target.value)}>
          <option value="">Top level</option>
          {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select>
      </div>
      <Button type="submit" variant="outline">Add category</Button>
      {error && <p className="w-full text-sm text-destructive">{error}</p>}
    </form>
  );
}

export default function SuppliersPage() {
  const me = useMe();
  const router = useRouter();
  const canManage = me.permissions.includes("suppliers:manage");
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const categories = useFetch<SupplierCategory[]>("supplier-categories");

  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(query);
      setOffset(0);
    }, 300);
    return () => clearTimeout(t);
  }, [query]);

  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (status) params.set("status", status);
  if (category) params.set("category_id", category);
  if (search) params.set("q", search);
  const list = useFetch<{ items: SupplierListItem[]; total: number }>(`suppliers?${params}`);
  const exportParams = new URLSearchParams();
  if (status) exportParams.set("status", status);
  if (category) exportParams.set("category_id", category);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Suppliers</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Only qualified suppliers can receive RFQs. Qualification follows your checklist and lapses when required evidence expires.
          </p>
        </div>
        <div className="flex gap-2">
          <Button asChild variant="outline"><a href={`/api/suppliers/export?${exportParams}`}>Export CSV</a></Button>
          {canManage && <Button onClick={() => setCreating(!creating)}>{creating ? "Close" : "New supplier"}</Button>}
        </div>
      </div>

      {creating && categories.data && (
        <Card>
          <CardHeader>
            <CardTitle>New supplier</CardTitle>
            <CardDescription>It starts as a prospect. Record the qualification checks on its page, then submit it for qualification.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <NewSupplier categories={categories.data} onCreated={(id) => router.push(`/suppliers/${id}`)} />
            <NewCategory categories={categories.data} onCreated={categories.reload} />
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap gap-3">
        <Input aria-label="Search by name or code" placeholder="Search by name or code" className="max-w-xs" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Select aria-label="Filter by status" className="w-auto" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
          <option value="">All statuses</option>
          {Object.entries(SUPPLIER_STATUS_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
        <Select aria-label="Filter by category" className="w-auto" value={category} onChange={(e) => { setCategory(e.target.value); setOffset(0); }}>
          <option value="">All categories</option>
          {categories.data?.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.supplier_count})</option>)}
        </Select>
      </div>

      {list.error && <Alert variant="error">{list.error.message}</Alert>}
      <Card>
        <CardContent className="overflow-x-auto pt-5">
          <table className="w-full text-left text-sm">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-2 pr-4 font-medium">Supplier</th>
                <th className="py-2 pr-4 font-medium">Status</th>
                <th className="py-2 pr-4 font-medium">Checklist</th>
                <th className="py-2 pr-4 font-medium">Categories</th>
                <th className="py-2 font-medium">Country</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {list.data?.items.map((s) => (
                <tr key={s.id}>
                  <td className="py-3 pr-4">
                    <Link href={`/suppliers/${s.id}`} className="font-medium text-primary hover:underline">{s.name}</Link>
                    <p className="font-mono text-xs text-muted-foreground">{s.code}</p>
                  </td>
                  <td className="py-3 pr-4"><SupplierStatusBadge status={s.status} qualified={s.qualification.qualified} /></td>
                  <td className="py-3 pr-4">
                    <div className="flex items-center gap-2">
                      <div className="h-2 w-20 overflow-hidden rounded-full bg-secondary" role="progressbar" aria-label="Qualification score" aria-valuenow={s.qualification.score} aria-valuemin={0} aria-valuemax={100}>
                        <div className="h-full bg-primary" style={{ width: `${s.qualification.score}%` }} />
                      </div>
                      <span className="text-xs text-muted-foreground">{s.qualification.score}%</span>
                    </div>
                    {s.qualification.missing_required.length > 0 && (
                      <p className="mt-1 text-xs text-caution">{s.qualification.missing_required.length} required check(s) open</p>
                    )}
                  </td>
                  <td className="py-3 pr-4">
                    <div className="flex flex-wrap gap-1">{s.categories.map((c) => <Badge key={c}>{c}</Badge>)}</div>
                  </td>
                  <td className="py-3">{s.country ?? "—"}</td>
                </tr>
              ))}
              {list.data && list.data.items.length === 0 && (
                <tr><td colSpan={5} className="py-8 text-center text-muted-foreground">No suppliers match.</td></tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
        <Button variant="outline" size="sm" disabled={!list.data || offset + PAGE >= list.data.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
      </div>
    </div>
  );
}
