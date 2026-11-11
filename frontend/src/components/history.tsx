import type { DomainEvent } from "@/lib/types";
import { formatDate } from "@/lib/utils";

export function History({ events }: { events: DomainEvent[] }) {
  if (events.length === 0) return <p className="text-sm text-muted-foreground">No history yet.</p>;
  return (
    <ol className="space-y-2 text-sm">
      {events.map((e) => (
        <li key={e.id} className="border-l-2 border-border pl-3">
          <p>
            <span className="font-medium">{e.action.replace(/_/g, " ")}</span>
            {e.to_state && e.from_state !== e.to_state ? <span className="text-muted-foreground"> → {e.to_state.replace(/_/g, " ")}</span> : null}
          </p>
          <p className="text-xs text-muted-foreground">
            {formatDate(e.created_at)} · {e.actor_email ?? "system"}
            {e.note ? ` · “${e.note}”` : ""}
          </p>
        </li>
      ))}
    </ol>
  );
}
