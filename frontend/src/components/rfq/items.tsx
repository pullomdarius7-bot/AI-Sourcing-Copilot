"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import type { RfqItem, RfqParam } from "@/lib/types";

const CATEGORIES = ["material", "dimensions", "tolerance", "quantity", "delivery", "quality", "compliance", "commercial", "packaging", "other"];
const PRIORITIES = ["mandatory", "preferred", "optional", "unknown"];

export function paramText(p: RfqParam) {
  const label = p.name.replace(/_/g, " ");
  if (p.unknown) return `${label}: not specified – supplier to state`;
  const prefix = { min: "≥ ", max: "≤ ", approx: "≈ " }[p.qualifier ?? ""] ?? "";
  return `${label}: ${prefix}${p.value}${p.unit ? ` ${p.unit}` : ""}`;
}

function ItemForm({ initial, onSubmit, onCancel, submitLabel }: {
  initial?: RfqItem; submitLabel: string; onCancel?: () => void;
  onSubmit: (body: Record<string, unknown>) => Promise<void>;
}) {
  const [statement, setStatement] = useState(initial?.statement ?? "");
  const [category, setCategory] = useState(initial?.category ?? "other");
  const [priority, setPriority] = useState(initial?.priority ?? "mandatory");
  const [quantity, setQuantity] = useState(initial?.quantity?.toString() ?? "");
  const [unit, setUnit] = useState(initial?.unit ?? "");
  const [error, setError] = useState<string | null>(null);
  return (
    <form
      className="grid gap-2 rounded-md bg-muted p-3 md:grid-cols-[1fr_9rem_9rem_7rem_6rem]"
      onSubmit={async (e) => {
        e.preventDefault();
        setError(null);
        try {
          await onSubmit({ statement, category, priority, quantity: quantity ? Number(quantity) : null, unit: unit || null });
        } catch (err) {
          setError(errorMessage(err));
        }
      }}
    >
      <Textarea aria-label="Requirement" className="min-h-[40px] md:col-span-5" value={statement} onChange={(e) => setStatement(e.target.value)} placeholder="What the supplier must provide" required minLength={3} />
      <Select aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
        {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
      </Select>
      <Select aria-label="Priority" value={priority} onChange={(e) => setPriority(e.target.value)}>
        {PRIORITIES.map((c) => <option key={c}>{c}</option>)}
      </Select>
      <Input aria-label="Quantity" type="number" step="any" min={0} placeholder="Quantity" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
      <Input aria-label="Unit" placeholder="Unit" value={unit} onChange={(e) => setUnit(e.target.value)} />
      <div className="flex gap-1 md:col-span-5">
        <Button type="submit" size="sm">{submitLabel}</Button>
        {onCancel && <Button type="button" size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>}
      </div>
      {error && <p className="text-sm text-destructive md:col-span-5">{error}</p>}
    </form>
  );
}

export function ItemsEditor({ rfqId, items, editable, onChanged }: { rfqId: string; items: RfqItem[]; editable: boolean; onChanged: () => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="space-y-3">
      {error && <p className="text-sm text-destructive">{error}</p>}
      <ol className="divide-y rounded-md border">
        {items.map((item, idx) => (
          <li key={item.id} className="space-y-2 px-3 py-3">
            {editing === item.id ? (
              <ItemForm
                initial={item}
                submitLabel="Save line"
                onCancel={() => setEditing(null)}
                onSubmit={async (body) => {
                  await api(`rfqs/${rfqId}/items/${item.id}`, { method: "PATCH", body });
                  setEditing(null);
                  onChanged();
                }}
              />
            ) : (
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm">
                    <span className="mr-2 font-mono text-xs text-muted-foreground">{item.line}.</span>
                    {item.statement}
                  </p>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    <Badge>{item.category}</Badge>
                    <Badge variant={item.priority === "mandatory" ? "default" : "neutral"}>{item.priority}</Badge>
                    {item.quantity != null && <Badge variant="neutral">{item.quantity} {item.unit ?? ""}</Badge>}
                    {item.source_requirement_id && <Badge variant="neutral" title="Taken from an approved requirement extraction">from requirements</Badge>}
                  </div>
                  {item.parameters.length > 0 && (
                    <ul className="mt-1 text-xs">
                      {item.parameters.map((p) => (
                        <li key={p.name} className={p.unknown ? "text-caution" : "text-muted-foreground"}>{paramText(p)}</li>
                      ))}
                    </ul>
                  )}
                </div>
                {editable && (
                  <div className="flex gap-1">
                    <Button size="sm" variant="ghost" disabled={idx === 0} aria-label="Move up" onClick={() => act(() => api(`rfqs/${rfqId}/items/${item.id}`, { method: "PATCH", body: { position: idx - 1 } }))}>↑</Button>
                    <Button size="sm" variant="ghost" disabled={idx === items.length - 1} aria-label="Move down" onClick={() => act(() => api(`rfqs/${rfqId}/items/${item.id}`, { method: "PATCH", body: { position: idx + 1 } }))}>↓</Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditing(item.id)}>Edit</Button>
                    <Button size="sm" variant="ghost" onClick={() => act(() => api(`rfqs/${rfqId}/items/${item.id}`, { method: "DELETE" }))}>Remove</Button>
                  </div>
                )}
              </div>
            )}
          </li>
        ))}
        {items.length === 0 && <li className="px-3 py-6 text-center text-sm text-muted-foreground">No lines yet.</li>}
      </ol>
      {editable && (adding ? (
        <ItemForm
          submitLabel="Add line"
          onCancel={() => setAdding(false)}
          onSubmit={async (body) => {
            await api(`rfqs/${rfqId}/items`, { body });
            setAdding(false);
            onChanged();
          }}
        />
      ) : (
        <Button variant="outline" size="sm" onClick={() => setAdding(true)}>Add line</Button>
      ))}
    </div>
  );
}
