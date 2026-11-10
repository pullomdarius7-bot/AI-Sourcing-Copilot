"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function AcceptInvitePage() {
  const [token, setToken] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setToken(new URLSearchParams(window.location.search).get("token") ?? "");
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("auth/accept-invitation", { body: { token, full_name: fullName, password } });
      setDone(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold">You are in</h1>
        <p className="text-sm text-muted-foreground">Your account is ready. Sign in to open the workspace.</p>
        <Button asChild className="w-full">
          <Link href="/login">Sign in</Link>
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Accept your invitation</h1>
        <p className="mt-1 text-sm text-muted-foreground">Set your name and password to join your team.</p>
      </div>
      {!token && <Alert variant="error">This link is missing its token. Ask your administrator to send a new invitation.</Alert>}
      {error && <Alert variant="error">{error}</Alert>}
      <div className="space-y-2">
        <Label htmlFor="name">Your name</Label>
        <Input id="name" value={fullName} onChange={(e) => setFullName(e.target.value)} autoComplete="name" required autoFocus />
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" required minLength={12} />
        <p className="text-xs text-muted-foreground">At least 12 characters, with a letter and a digit.</p>
      </div>
      <Button type="submit" className="w-full" disabled={busy || !token}>
        {busy ? "Joining…" : "Join organization"}
      </Button>
    </form>
  );
}
