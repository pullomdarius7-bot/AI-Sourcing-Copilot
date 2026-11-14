"use client";

import { useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

/** One click, no sign-in: stops all supplier invitations to this business. */
export default function UnsubscribePage() {
  const [state, setState] = useState<"ready" | "busy" | "done" | "error">("ready");
  const [message, setMessage] = useState("");

  async function confirm() {
    const token = new URLSearchParams(window.location.search).get("token");
    if (!token) {
      setState("error");
      setMessage("This link is missing its token.");
      return;
    }
    setState("busy");
    try {
      const out = await api<{ domain: string }>("invites/unsubscribe", { body: { token } });
      setState("done");
      setMessage(`We will not email ${out.domain} about buyer requests again.`);
    } catch (err) {
      setState("error");
      setMessage(errorMessage(err));
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Stop buyer request emails</h1>
      {state === "done" && <Alert variant="success">{message}</Alert>}
      {state === "error" && <Alert variant="error">{message}</Alert>}
      {(state === "ready" || state === "busy") && (
        <>
          <p className="text-sm text-muted-foreground">
            We emailed your business because buyers on our marketplace asked for products your website lists. Unsubscribing stops these
            emails for your address and your whole domain, permanently.
          </p>
          <Button className="w-full" onClick={() => void confirm()} disabled={state === "busy"}>
            {state === "busy" ? "Unsubscribing…" : "Unsubscribe"}
          </Button>
        </>
      )}
    </div>
  );
}
