"use client";

import Link from "next/link";
import { useState } from "react";
import { api, ApiError, errorMessage } from "@/lib/api";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [unverified, setUnverified] = useState(false);
  const [busy, setBusy] = useState(false);

  function destination() {
    const next = new URLSearchParams(window.location.search).get("next");
    return next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    setUnverified(false);
    try {
      if (mfaToken) {
        await api("auth/mfa/login", { body: { mfa_token: mfaToken, code } });
        window.location.href = destination();
        return;
      }
      const res = await api<{ mfa_required: boolean; mfa_token?: string }>("auth/login", { body: { email, password } });
      if (res.mfa_required && res.mfa_token) {
        setMfaToken(res.mfa_token);
      } else {
        window.location.href = destination();
        return;
      }
    } catch (err) {
      if (err instanceof ApiError && err.code === "email_not_verified") setUnverified(true);
      if (err instanceof ApiError && err.code === "invalid_mfa_token") setMfaToken(null);
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    try {
      await api("auth/resend-verification", { body: { email } });
      setNotice("If the address is registered, a new verification email is on its way.");
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">{mfaToken ? "Enter your code" : "Sign in"}</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {mfaToken
            ? "Open your authenticator app, or use one of your recovery codes."
            : "Use your work email to continue to your sourcing workspace."}
        </p>
      </div>
      {error && <Alert variant="error">{error}</Alert>}
      {notice && <Alert variant="success">{notice}</Alert>}
      {mfaToken ? (
        <div className="space-y-2">
          <Label htmlFor="code">Authentication or recovery code</Label>
          <Input id="code" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code" inputMode="text" autoFocus required />
        </div>
      ) : (
        <>
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required autoFocus />
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="password">Password</Label>
              <Link href="/forgot-password" className="text-xs text-primary hover:underline">
                Forgot password?
              </Link>
            </div>
            <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          </div>
        </>
      )}
      <Button type="submit" className="w-full" disabled={busy}>
        {busy ? "Signing in…" : mfaToken ? "Verify and sign in" : "Sign in"}
      </Button>
      {unverified && (
        <Button type="button" variant="outline" className="w-full" onClick={resend}>
          Resend verification email
        </Button>
      )}
      {mfaToken && (
        <Button type="button" variant="ghost" className="w-full" onClick={() => { setMfaToken(null); setCode(""); }}>
          Use a different account
        </Button>
      )}
      {!mfaToken && (
        <p className="text-sm text-muted-foreground">
          New here?{" "}
          <Link href="/register" className="text-primary hover:underline">
            Create an organization
          </Link>
        </p>
      )}
    </form>
  );
}
