"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { AssistantMessage } from "@/components/assistant/answer";
import { useMe } from "@/components/me-context";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { AgentConversation, AgentConversationDetail, AgentMessage, AgentStatus, AgentTurn } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";

// Starters that work in any organization; questions about a specific RFQ, supplier or file name it.
const STARTERS = [
  "What should we do next?",
  "How does this application score quotes?",
  "What is the difference between FOB and CIF?",
  "How is risk scored?",
  "What is a minimum order quantity?",
];

export default function AssistantPage() {
  const me = useMe();
  const status = useFetch<AgentStatus>("agent/status");
  const conversations = useFetch<AgentConversation[]>("agent/conversations");
  const [current, setCurrent] = useState<string | null>(null);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pending]);

  async function open(id: string | null) {
    setError(null);
    setCurrent(id);
    if (!id) {
      setMessages([]);
      return;
    }
    try {
      const detail = await api<AgentConversationDetail>(`agent/conversations/${id}`);
      setMessages(detail.messages);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function ask(text: string) {
    const q = text.trim();
    if (!q || pending) return;
    setError(null);
    setPending(q);
    setQuestion("");
    try {
      const turn = await api<AgentTurn>("agent/ask", { body: { question: q, conversation_id: current } });
      setCurrent(turn.conversation.id);
      setMessages((list) => [...list, turn.user, turn.assistant]);
      conversations.reload();
    } catch (err) {
      setError(errorMessage(err));
      setQuestion(q);
    } finally {
      setPending(null);
    }
  }

  async function feedback(id: string, rating: 1 | -1) {
    try {
      const updated = await api<AgentMessage>(`agent/messages/${id}/feedback`, { body: { rating } });
      setMessages((list) => list.map((m) => (m.id === id ? updated : m)));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function archive(id: string) {
    await api(`agent/conversations/${id}`, { method: "PATCH", body: { archived: true } });
    if (current === id) open(null);
    conversations.reload();
  }

  const s = status.data;
  return (
    <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
      <aside className="space-y-3">
        <Button className="w-full" variant="outline" onClick={() => open(null)}>New conversation</Button>
        <nav aria-label="Conversations" className="space-y-1">
          {conversations.data?.map((c) => (
            <div key={c.id} className={cn("group flex items-center rounded-md text-sm", current === c.id ? "bg-secondary" : "hover:bg-secondary/60")}>
              <button type="button" className="min-w-0 flex-1 truncate px-2 py-1.5 text-left" onClick={() => open(c.id)} title={c.title}>
                {c.title}
                <span className="block text-[11px] text-muted-foreground">{formatDate(c.updated_at)}</span>
              </button>
              <button type="button" className="px-2 text-xs text-muted-foreground opacity-0 group-hover:opacity-100" aria-label={`Archive ${c.title}`} onClick={() => archive(c.id)}>✕</button>
            </div>
          ))}
          {conversations.data?.length === 0 && <p className="px-2 text-xs text-muted-foreground">No conversations yet.</p>}
        </nav>
        {me.permissions.includes("agent:audit") && (
          <Link href="/assistant/runs" className="block px-2 text-xs text-primary hover:underline">All agent runs (audit)</Link>
        )}
      </aside>

      <section className="flex min-h-[70vh] flex-col">
        <header className="mb-4">
          <h1 className="text-2xl font-semibold tracking-tight">Sourcing assistant</h1>
          <p className="text-sm text-muted-foreground">
            Answers only from your organization&rsquo;s data and the built-in sourcing reference. Every statement cites its source,
            figures come from the application&rsquo;s deterministic calculations, and anything not in the data is reported as unknown.
            The assistant reads and explains; it never changes anything.
          </p>
          {s && (
            <p className="mt-1 text-xs text-muted-foreground">
              {s.mode === "llm" ? `Writing with ${s.providers.join(", ")} (output checked against the sources)` : "Deterministic mode – no AI model configured or needed"}
              {" · "}prompt {s.prompt_version} · {s.indexed_documents} document(s) searchable
              {s.unindexed_documents > 0 && `, ${s.unindexed_documents} indexed on first search`}
            </p>
          )}
        </header>

        <div className="flex-1 space-y-6">
          {messages.length === 0 && !pending && (
            <div className="space-y-2">
              <p className="text-sm font-medium">Try asking</p>
              <p className="text-xs text-muted-foreground">
                Name what you mean: “Compare the suppliers on RFQ-2026-0001”, “Why is Kessler ranked second?”, “Summarize quote-4711.pdf”,
                “Generate a status report for RFQ-2026-0001”.
              </p>
              <div className="flex flex-wrap gap-2">
                {STARTERS.map((q) => <Button key={q} size="sm" variant="outline" onClick={() => ask(q)}>{q}</Button>)}
              </div>
            </div>
          )}
          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="ml-auto max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">{m.content}</div>
            ) : (
              <div key={m.id} className="rounded-lg border p-4"><AssistantMessage message={m} onAsk={ask} onFeedback={feedback} /></div>
            ),
          )}
          {pending && (
            <>
              <div className="ml-auto max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">{pending}</div>
              <div className="rounded-lg border p-4 text-sm text-muted-foreground" role="status">Looking it up…</div>
            </>
          )}
          <div ref={bottom} />
        </div>

        <form className="sticky bottom-0 mt-4 space-y-2 bg-background pb-2 pt-2" onSubmit={(e) => { e.preventDefault(); ask(question); }}>
          {error && <Alert variant="error">{error}</Alert>}
          <Textarea aria-label="Your question" rows={3} maxLength={4000} placeholder="Ask about an RFQ, a supplier, a document or a sourcing term…"
            value={question} onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(question); } }} />
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">Enter to send, Shift+Enter for a new line.</p>
            <Button type="submit" disabled={!question.trim() || !!pending}>{pending ? "Working…" : "Ask"}</Button>
          </div>
        </form>
      </section>
    </div>
  );
}
