"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { AccountType } from "@/lib/types";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const ROLES: AccountType[] = ["solo_buyer", "org_buyer", "supplier"];
const INTRO: Record<AccountType, { title: string; text: string; button: string }> = {
  solo_buyer: {
    title: "Start your 7-day free trial",
    text: "Includes 10 free manifest analyses. Set up takes about a minute.",
    button: "Start free trial",
  },
  org_buyer: {
    title: "Set up your sourcing team",
    text: "Create your account, then invite your team. Starts with a 7-day trial and 10 manifest analyses.",
    button: "Create team account",
  },
  supplier: {
    title: "List as a verified supplier",
    text: "Create your account, tell us what you sell, and get your inventory in front of active buyers.",
    button: "Create supplier account",
  },
};

export default function RegisterPage() {
  // The landing page links here with ?tier=trial or ?role=supplier; the role is confirmed during onboarding.
  const [role, setRole] = useState<AccountType>("solo_buyer");
  const [form, setForm] = useState({ full_name: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  const [invite, setInvite] = useState<{ token: string; company_name: string; request_count: number; demand: { title: string; quantity: number | null }[] } | null>(null);

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("invite");
    if (!token) return;
    api<{ company_name: string; request_count: number; demand: { title: string; quantity: number | null }[] }>(`invites/preview?token=${encodeURIComponent(token)}`)
      .then((preview) => {
        setInvite({ token, ...preview });
        setRole("supplier");
      })
      .catch(() => setInvite(null));
  }, []);

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("role");
    if (requested && (ROLES as string[]).includes(requested)) setRole(requested as AccountType);
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("auth/register", {
        body: { ...form, account_type: role, organization_name: invite?.company_name, invite_token: invite?.token },
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
          We sent a verification link to <strong>{form.email}</strong>. Open it, sign in, and we will finish setting up your account.
        </p>
        <Button asChild className="w-full">
          <Link href="/login?next=/onboarding">Go to sign in</Link>
        </Button>
      </div>
    );
  }

  const intro = INTRO[role];
  return (
    <form onSubmit={submit} className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">{intro.title}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{intro.text}</p>
      </div>
      {invite && (
        <Alert>
          <strong>{invite.request_count} buyer request{invite.request_count === 1 ? "" : "s"}</strong> for products {invite.company_name} sells
          {invite.demand[0] ? `, including "${invite.demand[0].title}"` : ""}. Create your free supplier account to answer them, with 75% off
          your first month of the Verified Supplier plan.
        </Alert>
      )}
      {error && <Alert variant="error">{error}</Alert>}
      <div className="space-y-2">
        <Label htmlFor="name">Your name</Label>
        <Input id="name" value={form.full_name} onChange={set("full_name")} autoComplete="name" required autoFocus />
      </div>
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input id="email" type="email" value={form.email} onChange={set("email")} autoComplete="username" required />
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input id="password" type="password" value={form.password} onChange={set("password")} autoComplete="new-password" required minLength={12} aria-describedby="password-help" />
        <p id="password-help" className="text-xs text-muted-foreground">At least 12 characters, with a letter and a digit.</p>
      </div>
      <Button type="submit" className="w-full" disabled={busy}>
        {busy ? "Creating…" : intro.button}
      </Button>
      <p className="text-sm text-muted-foreground">
        {role === "supplier" ? (
          <>Looking to buy instead? <Link href="/register?tier=trial" onClick={() => setRole("solo_buyer")} className="text-primary hover:underline">Start a buyer trial</Link></>
        ) : (
          <>Selling inventory? <Link href="/register?role=supplier" onClick={() => setRole("supplier")} className="text-primary hover:underline">List as a supplier</Link></>
        )}
      </p>
      <p className="text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link href="/login" className="text-primary hover:underline">
          Sign in
        </Link>
      </p>
    </form>
  );
}
