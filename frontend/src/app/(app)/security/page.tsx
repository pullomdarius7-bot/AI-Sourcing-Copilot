"use client";

import { QRCodeSVG } from "qrcode.react";
import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useMe } from "@/components/me-context";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { SessionInfo } from "@/lib/types";
import { formatDate } from "@/lib/utils";

function MfaCard() {
  const me = useMe();
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState<string[] | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function attempt(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Two-factor authentication</CardTitle>
          <Badge variant={me.mfa_enabled ? "default" : "caution"}>{me.mfa_enabled ? "On" : "Off"}</Badge>
        </div>
        <CardDescription>Use an authenticator app (TOTP) to protect sign-in even if your password leaks.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <Alert variant="error">{error}</Alert>}

        {recovery && (
          <div className="space-y-3">
            <Alert variant="success">Two-factor authentication is on. Save these recovery codes now; each works once and they will not be shown again.</Alert>
            <ul className="grid grid-cols-2 gap-2 rounded-md border bg-muted p-3 font-mono text-sm">
              {recovery.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
            <Button variant="outline" onClick={() => window.location.reload()}>
              I saved them
            </Button>
          </div>
        )}

        {!recovery && !me.mfa_enabled && !setup && (
          <Button onClick={() => attempt(async () => setSetup(await api("auth/mfa/setup", { method: "POST" })))} disabled={busy}>
            Set up authenticator app
          </Button>
        )}

        {!recovery && !me.mfa_enabled && setup && (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void attempt(async () => {
                const res = await api<{ recovery_codes: string[] }>("auth/mfa/enable", { body: { code } });
                setRecovery(res.recovery_codes);
              });
            }}
          >
            <p className="text-sm text-muted-foreground">Scan the code with your authenticator app, then enter the 6-digit code it shows.</p>
            <div className="inline-block rounded-md border bg-white p-3">
              <QRCodeSVG value={setup.otpauth_uri} size={176} title="Authenticator setup code" />
            </div>
            <p className="text-xs text-muted-foreground">
              Cannot scan? Enter this key manually: <span className="break-all font-mono text-foreground">{setup.secret}</span>
            </p>
            <div className="max-w-xs space-y-2">
              <Label htmlFor="mfa-code">6-digit code</Label>
              <Input id="mfa-code" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={6} required />
            </div>
            <Button type="submit" disabled={busy}>
              Turn on two-factor authentication
            </Button>
          </form>
        )}

        {!recovery && me.mfa_enabled && (
          <form
            className="max-w-xs space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              void attempt(async () => {
                await api("auth/mfa/disable", { body: { password, code } });
                window.location.reload();
              });
            }}
          >
            <div className="space-y-2">
              <Label htmlFor="mfa-pw">Password</Label>
              <Input id="mfa-pw" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="mfa-off-code">Authentication or recovery code</Label>
              <Input id="mfa-off-code" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code" required />
            </div>
            <Button type="submit" variant="destructive" disabled={busy}>
              Turn off two-factor authentication
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

function PasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    try {
      const res = await api<{ message: string }>("auth/password/change", { body: { current_password: current, new_password: next } });
      setNotice(res.message);
      setCurrent("");
      setNext("");
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Password</CardTitle>
        <CardDescription>Changing your password signs out every other device.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="max-w-xs space-y-3">
          {error && <Alert variant="error">{error}</Alert>}
          {notice && <Alert variant="success">{notice}</Alert>}
          <div className="space-y-2">
            <Label htmlFor="current">Current password</Label>
            <Input id="current" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new">New password</Label>
            <Input id="new" type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={12} required />
          </div>
          <Button type="submit">Change password</Button>
        </form>
      </CardContent>
    </Card>
  );
}

function SessionsCard() {
  const { data, error, reload } = useFetch<SessionInfo[]>("auth/sessions");
  const [actionError, setActionError] = useState<string | null>(null);

  async function revoke(id: string) {
    setActionError(null);
    try {
      await api(`auth/sessions/${id}`, { method: "DELETE" });
      reload();
    } catch (err) {
      setActionError(errorMessage(err));
    }
  }

  async function signOutEverywhere() {
    try {
      await api("auth/logout-all", { method: "POST" });
    } finally {
      window.location.href = "/login";
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Active sessions</CardTitle>
        <CardDescription>Devices currently signed in to your account.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {(error || actionError) && <Alert variant="error">{actionError ?? error?.message}</Alert>}
        <ul className="divide-y rounded-md border">
          {data?.map((s) => (
            <li key={s.id} className="flex items-center justify-between gap-3 px-3 py-3 text-sm">
              <div className="min-w-0">
                <p className="truncate font-medium">
                  {s.user_agent ?? "Unknown device"} {s.current && <Badge className="ml-2">This device</Badge>}
                </p>
                <p className="text-xs text-muted-foreground">
                  {s.ip ?? "unknown IP"} · last active {formatDate(s.last_used_at)}
                </p>
              </div>
              {!s.current && (
                <Button variant="outline" size="sm" onClick={() => revoke(s.id)}>
                  Sign out
                </Button>
              )}
            </li>
          ))}
        </ul>
        <Button variant="outline" onClick={signOutEverywhere}>
          Sign out everywhere
        </Button>
      </CardContent>
    </Card>
  );
}

export default function SecurityPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Security</h1>
      <MfaCard />
      <PasswordCard />
      <SessionsCard />
    </div>
  );
}
