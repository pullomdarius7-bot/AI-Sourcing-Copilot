"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export default function VerifyEmailPage() {
  const [state, setState] = useState<"working" | "ok" | "error">("working");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("token");
    if (!token) {
      setState("error");
      setMessage("This link is missing its token.");
      return;
    }
    api("auth/verify-email", { body: { token } })
      .then(() => setState("ok"))
      .catch((err) => {
        setState("error");
        setMessage(errorMessage(err));
      });
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Email verification</h1>
      {state === "working" && <p className="text-sm text-muted-foreground">Verifying…</p>}
      {state === "ok" && <Alert variant="success">Your email address is verified. Sign in to finish setting up your account.</Alert>}
      {state === "error" && <Alert variant="error">{message} Sign in to request a new verification email.</Alert>}
      <Button asChild className="w-full">
        <Link href="/login?next=/onboarding">Go to sign in</Link>
      </Button>
    </div>
  );
}
