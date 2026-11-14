"use client";

import { useEffect, useRef, useState } from "react";
import { Building2, Check, Loader2, Store, Warehouse } from "lucide-react";
import { api, errorMessage, useFetch } from "@/lib/api";
import type { AccountType, OnboardingField, OnboardingState } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";

const STEPS = ["Your role", "Categories & scale", "Account setup"];
const ROLE_ICONS: Record<AccountType, typeof Store> = { solo_buyer: Store, org_buyer: Building2, supplier: Warehouse };
const STEP2_TITLE: Record<AccountType, string> = {
  solo_buyer: "What are you sourcing?",
  org_buyer: "What is your team sourcing?",
  supplier: "What inventory do you sell?",
};

type Answers = Record<string, string | string[]>;
type Errors = Record<string, string>;

function chosen(answers: Answers, key: string): string[] {
  const value = answers[key];
  return Array.isArray(value) ? value : [];
}

export default function OnboardingPage() {
  const { data, error, loading } = useFetch<OnboardingState>("onboarding");
  const [step, setStep] = useState(0);
  const [role, setRole] = useState<AccountType | null>(null);
  const [company, setCompany] = useState("");
  const [answers, setAnswers] = useState<Answers>({});
  const [errors, setErrors] = useState<Errors>({});
  const [problem, setProblem] = useState<string | null>(null);
  const [provisioned, setProvisioned] = useState(0); // how many provisioning lines are done
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (!data) return;
    if (data.completed) {
      window.location.replace("/dashboard");
      return;
    }
    setRole(data.account_type);
  }, [data]);

  // Move focus to the step heading so keyboard and screen-reader users land on the new step.
  useEffect(() => {
    heading.current?.focus();
  }, [step]);

  async function signOut() {
    try {
      await api("auth/logout", { method: "POST" });
    } finally {
      window.location.href = "/login";
    }
  }

  if (loading || data?.completed) return <div className="p-8 text-sm text-muted-foreground">Loading…</div>;
  if (error || !data) return <div className="p-8 text-sm text-destructive">{error?.message ?? "Could not load your account."}</div>;

  if (!data.can_edit) {
    return (
      <main className="mx-auto max-w-xl space-y-4 px-5 py-16">
        <Alert>An administrator of your account needs to finish setting it up before you can continue.</Alert>
        <Button variant="outline" onClick={signOut}>Sign out</Button>
      </main>
    );
  }

  const fields: OnboardingField[] = role ? data.forms[role] : [];
  const needsCompany = role === "org_buyer" || role === "supplier";

  function chooseRole(next: AccountType) {
    if (next !== role) {
      setAnswers({});
      setErrors({});
    }
    setRole(next);
  }

  function toggle(key: string, value: string) {
    const current = chosen(answers, key);
    setAnswers({ ...answers, [key]: current.includes(value) ? current.filter((v) => v !== value) : [...current, value] });
    setErrors({ ...errors, [key]: "" });
  }

  function validate(): Errors {
    const found: Errors = {};
    if (needsCompany && company.trim().length < 2) found.company = "Enter your company name.";
    for (const f of fields) {
      if (f.kind === "multiselect" ? chosen(answers, f.key).length === 0 : !answers[f.key]) {
        found[f.key] = f.kind === "multiselect" ? "Choose at least one." : "Choose an option.";
      }
    }
    return found;
  }

  async function provision(e: React.FormEvent) {
    e.preventDefault();
    const found = validate();
    setErrors(found);
    const first = Object.keys(found)[0];
    if (first) {
      document.getElementById(`field-${first}`)?.focus();
      return;
    }
    setProblem(null);
    setProvisioned(0);
    setStep(2);
    try {
      const body = { account_type: role, organization_name: needsCompany ? company.trim() : undefined, answers };
      const [result] = await Promise.all([
        api<OnboardingState>("onboarding", { method: "PUT", body }),
        new Promise((r) => setTimeout(r, 500)),
      ]);
      setProvisioned(1);
      await new Promise((r) => setTimeout(r, 450));
      setProvisioned(2);
      await new Promise((r) => setTimeout(r, 450));
      setProvisioned(result.completed ? 3 : 2);
      window.location.href = "/dashboard";
    } catch (err) {
      setProblem(errorMessage(err));
      setStep(1);
    }
  }

  const provisioning = ["Saving your sourcing profile", "Activating your 7-day trial with 10 manifest analyses", "Opening your dashboard"];

  return (
    <main className="mx-auto min-h-screen max-w-2xl px-5 py-10 md:py-16">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold">Sourcing Copilot</p>
        {step < 2 && <Button variant="ghost" size="sm" onClick={signOut}>Sign out</Button>}
      </div>

      <ol aria-label="Progress" className="mt-8 grid grid-cols-3 gap-2">
        {STEPS.map((label, i) => (
          <li key={label} aria-current={i === step ? "step" : undefined}>
            <div className={cn("h-1.5 rounded-full", i <= step ? "bg-primary" : "bg-secondary")} />
            <p className={cn("mt-2 text-xs", i === step ? "font-medium text-foreground" : "text-muted-foreground")}>
              <span className="sr-only">Step </span>{i + 1}<span className="hidden sm:inline">. {label}</span>
            </p>
          </li>
        ))}
      </ol>

      <div className="mt-8 rounded-xl border bg-card p-6 md:p-8">
        {step === 0 && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (role) setStep(1);
            }}
          >
            <h1 ref={heading} tabIndex={-1} className="text-xl font-semibold outline-none">How will you use Sourcing Copilot?</h1>
            <p className="mt-1 text-sm text-muted-foreground">This sets up the right workspace. It cannot be changed later.</p>
            <fieldset className="mt-6 space-y-3">
              <legend className="sr-only">Your role</legend>
              {data.roles.map((r) => {
                const Icon = ROLE_ICONS[r.value];
                const selected = role === r.value;
                return (
                  <label
                    key={r.value}
                    className={cn(
                      "flex cursor-pointer items-center gap-4 rounded-lg border p-4 transition-colors focus-within:ring-2 focus-within:ring-ring",
                      selected ? "border-primary bg-primary/5" : "hover:bg-secondary/60",
                    )}
                  >
                    <input type="radio" name="role" value={r.value} checked={selected} onChange={() => chooseRole(r.value)} className="sr-only" />
                    <span className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-lg", selected ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground")}>
                      <Icon className="h-5 w-5" aria-hidden />
                    </span>
                    <span className="flex-1">
                      <span className="block text-sm font-medium">{r.label}</span>
                      <span className="block text-sm text-muted-foreground">{r.description}</span>
                    </span>
                    <span className={cn("flex h-5 w-5 shrink-0 items-center justify-center rounded-full border", selected && "border-primary bg-primary text-primary-foreground")} aria-hidden>
                      {selected && <Check className="h-3 w-3" />}
                    </span>
                  </label>
                );
              })}
            </fieldset>
            <div className="mt-8 flex justify-end">
              <Button type="submit" disabled={!role}>Continue</Button>
            </div>
          </form>
        )}

        {step === 1 && role && (
          <form onSubmit={provision} noValidate>
            <h1 ref={heading} tabIndex={-1} className="text-xl font-semibold outline-none">{STEP2_TITLE[role]}</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {role === "supplier" ? "We use this to match your lots with the right buyers." : "We use this to tune deal analysis to how you buy."}
            </p>
            {problem && <Alert variant="error" className="mt-5">{problem}</Alert>}
            <div className="mt-6 space-y-7">
              {needsCompany && (
                <div className="space-y-2">
                  <Label htmlFor="field-company">Company name</Label>
                  <Input
                    id="field-company"
                    value={company}
                    onChange={(e) => {
                      setCompany(e.target.value);
                      setErrors({ ...errors, company: "" });
                    }}
                    autoComplete="organization"
                    aria-invalid={!!errors.company}
                    aria-describedby={errors.company ? "error-company" : undefined}
                  />
                  {errors.company && <p id="error-company" className="text-sm text-destructive">{errors.company}</p>}
                </div>
              )}
              {fields.map((field) =>
                field.kind === "select" ? (
                  <div key={field.key} className="space-y-2">
                    <Label htmlFor={`field-${field.key}`}>{field.label}</Label>
                    <Select
                      id={`field-${field.key}`}
                      value={(answers[field.key] as string | undefined) ?? ""}
                      onChange={(e) => {
                        setAnswers({ ...answers, [field.key]: e.target.value });
                        setErrors({ ...errors, [field.key]: "" });
                      }}
                      aria-invalid={!!errors[field.key]}
                      aria-describedby={errors[field.key] ? `error-${field.key}` : undefined}
                    >
                      <option value="" disabled>Choose…</option>
                      {field.options.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </Select>
                    {errors[field.key] && <p id={`error-${field.key}`} className="text-sm text-destructive">{errors[field.key]}</p>}
                  </div>
                ) : (
                  <fieldset key={field.key} aria-describedby={errors[field.key] ? `error-${field.key}` : undefined}>
                    <legend className="text-sm font-medium">{field.label}</legend>
                    {field.help && <p className="mt-1 text-xs text-muted-foreground">{field.help}</p>}
                    <div className="mt-3 flex flex-wrap gap-2">
                      {field.options.map((o, i) => {
                        const on = chosen(answers, field.key).includes(o.value);
                        return (
                          <label
                            key={o.value}
                            className={cn(
                              "inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-3.5 py-2 text-sm transition-colors focus-within:ring-2 focus-within:ring-ring",
                              on ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-secondary",
                            )}
                          >
                            <input
                              type="checkbox"
                              id={i === 0 ? `field-${field.key}` : undefined}
                              checked={on}
                              onChange={() => toggle(field.key, o.value)}
                              className="sr-only"
                            />
                            {on && <Check className="h-3.5 w-3.5" aria-hidden />}
                            {o.label}
                          </label>
                        );
                      })}
                    </div>
                    {errors[field.key] && <p id={`error-${field.key}`} className="mt-2 text-sm text-destructive">{errors[field.key]}</p>}
                  </fieldset>
                ),
              )}
            </div>
            <div className="mt-8 flex items-center justify-between gap-3">
              <Button type="button" variant="ghost" onClick={() => setStep(0)}>Back</Button>
              <Button type="submit">Finish setup</Button>
            </div>
          </form>
        )}

        {step === 2 && (
          <div aria-live="polite">
            <h1 ref={heading} tabIndex={-1} className="text-xl font-semibold outline-none">Setting up your account</h1>
            <p className="mt-1 text-sm text-muted-foreground">This only takes a moment.</p>
            <ul className="mt-6 space-y-4">
              {provisioning.map((line, i) => (
                <li key={line} className="flex items-center gap-3 text-sm">
                  {i < provisioned ? (
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-primary-foreground">
                      <Check className="h-3.5 w-3.5" aria-hidden />
                    </span>
                  ) : i === provisioned ? (
                    <Loader2 className="h-6 w-6 animate-spin text-primary" aria-hidden />
                  ) : (
                    <span className="h-6 w-6 rounded-full border" aria-hidden />
                  )}
                  <span className={cn(i > provisioned && "text-muted-foreground")}>{line}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </main>
  );
}
