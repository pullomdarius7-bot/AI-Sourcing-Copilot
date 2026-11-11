"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function ResetPasswordPage() {
  const [token, setToken] = useState("");
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
      await api("auth/password/reset", { body: { token, new_password: password } });
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
        <h1 className="text-2xl font-semibold">Password updated</h1>
        <p className="text-sm text-muted-foreground">All other sessions were signed out. Sign in with your new password.</p>
        <Button asChild className="w-full">
          <Link href="/login">Sign in</Link>
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      <h1 className="text-2xl font-semibold">Choose a new password</h1>
      {!token && <Alert variant="error">This link is missing its token. Request a new reset email.</Alert>}
      {error && <Alert variant="error">{error}</Alert>}
      <div className="space-y-2">
        <Label htmlFor="password">New password</Label>
        <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" required minLength={12} autoFocus />
        <p className="text-xs text-muted-foreground">At least 12 characters, with a letter and a digit.</p>
      </div>
      <Button type="submit" className="w-full" disabled={busy || !token}>
        {busy ? "Saving…" : "Update password"}
      </Button>
    </form>
  );
}
