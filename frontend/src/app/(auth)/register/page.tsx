"use client";

import Link from "next/link";
import { useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { AccountType } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const ACCOUNT_TYPES: { value: AccountType; label: string; text: string }[] = [
  { value: "solo_buyer", label: "Solo buyer", text: "I source inventory for my own resale business." },
  { value: "org_buyer", label: "Buying team", text: "We source as a company, with members and approvals." },
  { value: "supplier", label: "Supplier", text: "We sell wholesale or liquidation inventory." },
];

export default function RegisterPage() {
  const [accountType, setAccountType] = useState<AccountType>("solo_buyer");
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
      const solo = accountType === "solo_buyer";
      await api("auth/register", {
        body: { ...form, account_type: accountType, organization_name: solo ? undefined : form.organization_name },
      });
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
        <h1 className="text-2xl font-semibold">Create your account</h1>
        <p className="mt-1 text-sm text-muted-foreground">Free to start. After verifying your email we will ask a few questions about your business.</p>
      </div>
      {error && <Alert variant="error">{error}</Alert>}
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">I am a…</legend>
        {ACCOUNT_TYPES.map((t) => (
          <label
            key={t.value}
            className={cn(
              "flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2",
              accountType === t.value && "border-primary bg-primary/5",
            )}
          >
            <input
              type="radio"
              name="account_type"
              className="mt-1 accent-primary"
              checked={accountType === t.value}
              onChange={() => setAccountType(t.value)}
            />
            <span>
              <span className="block text-sm font-medium">{t.label}</span>
              <span className="block text-xs text-muted-foreground">{t.text}</span>
            </span>
          </label>
        ))}
      </fieldset>
      {accountType !== "solo_buyer" && (
        <div className="space-y-2">
          <Label htmlFor="org">{accountType === "supplier" ? "Company name" : "Organization name"}</Label>
          <Input id="org" value={form.organization_name} onChange={set("organization_name")} required minLength={2} />
        </div>
      )}
      <div className="space-y-2">
        <Label htmlFor="name">Your name</Label>
        <Input id="name" value={form.full_name} onChange={set("full_name")} autoComplete="name" required />
      </div>
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input id="email" type="email" value={form.email} onChange={set("email")} autoComplete="username" required />
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input id="password" type="password" value={form.password} onChange={set("password")} autoComplete="new-password" required minLength={12} />
        <p className="text-xs text-muted-foreground">At least 12 characters, with a letter and a digit.</p>
      </div>
      <Button type="submit" className="w-full" disabled={busy}>
        {busy ? "Creating…" : "Create account"}
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
