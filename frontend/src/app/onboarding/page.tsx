"use client";

import { useEffect, useState } from "react";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { AccountType, OnboardingState } from "@/lib/types";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";

const INTRO: Record<AccountType, { title: string; text: string }> = {
  solo_buyer: { title: "Tell us how you source", text: "A few questions so we can tune deal analysis to your resale business." },
  org_buyer: { title: "Tell us about your team", text: "A few questions so we can set up your procurement workspace." },
  supplier: { title: "Tell us what you sell", text: "A few questions so we can match your inventory with the right buyers." },
};

type Answers = Record<string, string | string[]>;

export default function OnboardingPage() {
  const { data, error, loading } = useFetch<OnboardingState>("onboarding");
  const [answers, setAnswers] = useState<Answers>({});
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (data) setAnswers(data.profile);
  }, [data]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!data) return;
    const missing = data.fields.find((f) => (f.kind === "multiselect" ? !(answers[f.key] as string[] | undefined)?.length : !answers[f.key]));
    if (missing) {
      setProblem(`Please answer: ${missing.label}`);
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      await api("onboarding", { method: "PUT", body: { answers } });
      window.location.href = "/dashboard";
    } catch (err) {
      setProblem(errorMessage(err));
      setBusy(false);
    }
  }

  async function signOut() {
    try {
      await api("auth/logout", { method: "POST" });
    } finally {
      window.location.href = "/login";
    }
  }

  function toggle(key: string, value: string) {
    const current = (answers[key] as string[] | undefined) ?? [];
    setAnswers({ ...answers, [key]: current.includes(value) ? current.filter((v) => v !== value) : [...current, value] });
  }

  if (loading) return <div className="p-8 text-sm text-muted-foreground">Loading…</div>;
  if (error || !data) return <div className="p-8 text-sm text-destructive">{error?.message ?? "Could not load your account."}</div>;

  const intro = INTRO[data.account_type];
  return (
    <main className="mx-auto flex min-h-screen max-w-xl items-center px-6 py-12">
      <Card className="w-full">
        <CardHeader>
          <CardTitle>{intro.title}</CardTitle>
          <CardDescription>{intro.text}</CardDescription>
        </CardHeader>
        <CardContent>
          {!data.can_edit ? (
            <div className="space-y-4">
              <Alert>An administrator of your account needs to complete its profile before you can continue.</Alert>
              <Button variant="outline" onClick={signOut}>Sign out</Button>
            </div>
          ) : (
            <form onSubmit={submit} className="space-y-6">
              {problem && <Alert variant="error">{problem}</Alert>}
              {data.fields.map((field) =>
                field.kind === "select" ? (
                  <div key={field.key} className="space-y-2">
                    <Label htmlFor={field.key}>{field.label}</Label>
                    <Select
                      id={field.key}
                      value={(answers[field.key] as string | undefined) ?? ""}
                      onChange={(e) => setAnswers({ ...answers, [field.key]: e.target.value })}
                      required
                    >
                      <option value="" disabled>Choose…</option>
                      {field.options.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </Select>
                  </div>
                ) : (
                  <fieldset key={field.key} className="space-y-2">
                    <legend className="text-sm font-medium">{field.label}</legend>
                    {field.help && <p className="text-xs text-muted-foreground">{field.help}</p>}
                    <div className="grid gap-2 sm:grid-cols-2">
                      {field.options.map((o) => (
                        <label key={o.value} className="flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm has-[:checked]:border-primary has-[:checked]:bg-primary/5">
                          <input
                            type="checkbox"
                            className="h-4 w-4 accent-primary"
                            checked={((answers[field.key] as string[] | undefined) ?? []).includes(o.value)}
                            onChange={() => toggle(field.key, o.value)}
                          />
                          {o.label}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                ),
              )}
              <div className="flex items-center justify-between gap-3">
                <Button type="button" variant="ghost" onClick={signOut}>Sign out</Button>
                <Button type="submit" disabled={busy}>{busy ? "Saving…" : data.completed ? "Save" : "Continue"}</Button>
              </div>
            </form>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
