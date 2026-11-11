"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useMe } from "@/components/me-context";
import { useFetch } from "@/lib/api";
import type { SessionInfo } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const ROLE_SUMMARY: Record<string, string> = {
  requester: "Raise sourcing requirements and follow their progress.",
  buyer: "Run sourcing events and compare supplier quotes.",
  approver: "Review recommendations and make the final supplier decision.",
  admin: "Manage the organization, its people and their access.",
  auditor: "Read-only access to people and the audit trail.",
};

export default function DashboardPage() {
  const me = useMe();
  const { data: sessions } = useFetch<SessionInfo[]>("auth/sessions");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Welcome, {me.full_name.split(" ")[0]}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{ROLE_SUMMARY[me.role]}</p>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardDescription>Email</CardDescription>
            <CardTitle>{me.email_verified ? "Verified" : "Not verified"}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">{me.email}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>Two-factor authentication</CardDescription>
            <CardTitle>{me.mfa_enabled ? "On" : "Off"}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            {me.mfa_enabled ? (
              <span className="text-muted-foreground">Your account asks for a code at sign-in.</span>
            ) : (
              <Link href="/security" className="text-primary hover:underline">
                Add an authenticator app
              </Link>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardDescription>Active sessions</CardDescription>
            <CardTitle>{sessions ? sessions.length : "…"}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">Last sign-in {formatDate(me.last_login_at)}</CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>What your role can do</CardTitle>
          <CardDescription>Permissions granted to the {me.role} role in {me.organization_name}.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {me.permissions.map((p) => (
            <Badge key={p} className="font-mono">
              {p}
            </Badge>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
