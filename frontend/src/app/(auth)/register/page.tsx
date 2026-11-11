"use client";

import Link from "next/link";
import { useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function RegisterPage() {
  const [form, setForm] = useState({ organization_name: "", full_name: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("auth/register", { body: form });
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
        <h1 className="text-2xl font-semibold">Check your inbox</h1>
        <p className="text-sm text-muted-foreground">
          We sent a verification link to <strong>{form.email}</strong>. Open it to activate your account, then sign in.
        </p>
        <Button asChild className="w-full">
          <Link href="/login">Go to sign in</Link>
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Create your organization</h1>
        <p className="mt-1 text-sm text-muted-foreground">You will be its first administrator and can invite your team afterwards.</p>
      </div>
      {error && <Alert variant="error">{error}</Alert>}
      <div className="space-y-2">
        <Label htmlFor="org">Organization name</Label>
        <Input id="org" value={form.organization_name} onChange={set("organization_name")} required minLength={2} autoFocus />
      </div>
      <div className="space-y-2">
        <Label htmlFor="name">Your name</Label>
        <Input id="name" value={form.full_name} onChange={set("full_name")} autoComplete="name" required />
      </div>
      <div className="space-y-2">
        <Label htmlFor="email">Work email</Label>
        <Input id="email" type="email" value={form.email} onChange={set("email")} autoComplete="username" required />
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input id="password" type="password" value={form.password} onChange={set("password")} autoComplete="new-password" required minLength={12} />
        <p className="text-xs text-muted-foreground">At least 12 characters, with a letter and a digit.</p>
      </div>
      <Button type="submit" className="w-full" disabled={busy}>
        {busy ? "Creating…" : "Create organization"}
      </Button>
      <p className="text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link href="/login" className="text-primary hover:underline">
          Sign in
        </Link>
      </p>
    </form>
  );
}
