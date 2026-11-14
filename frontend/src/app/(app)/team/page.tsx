"use client";

import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useMe } from "@/components/me-context";
import { api, errorMessage, useFetch } from "@/lib/api";
import { ROLES, type Invitation, type Role, type TeamUser } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const selectClass = "h-9 rounded-md border border-input bg-card px-2 text-sm";

export default function TeamPage() {
  const me = useMe();
  const canManage = me.permissions.includes("users:manage");
  const canInvite = me.permissions.includes("invitations:manage");
  const users = useFetch<TeamUser[]>("users");
  const seats = useFetch<{ used: number; limit: number | null }>("seats");
  const full = seats.data?.limit != null && seats.data.used >= seats.data.limit;
  const invites = useFetch<Invitation[]>(canInvite ? "invitations" : null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<Role>("buyer");
  const [orgName, setOrgName] = useState(me.organization_name);

  async function run(action: () => Promise<unknown>, success: string, after: () => void) {
    setError(null);
    setNotice(null);
    try {
      await action();
      setNotice(success);
      after();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const update = (id: string, patch: Record<string, unknown>, success: string) =>
    run(() => api(`users/${id}`, { method: "PATCH", body: patch }), success, () => { users.reload(); seats.reload(); });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Team</h1>
      {error && <Alert variant="error">{error}</Alert>}
      {notice && <Alert variant="success">{notice}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle>People</CardTitle>
          <CardDescription>
            Everyone with access to {me.organization_name}.
            {seats.data?.limit != null && (
              <> {seats.data.used} of {seats.data.limit} seats used (active people plus open invitations).
                {full && <> All seats are taken: deactivate someone, revoke an invitation or <a href="/billing" className="text-primary hover:underline">upgrade the plan</a>.</>}
              </>
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-2 pr-4 font-medium">Name</th>
                <th className="py-2 pr-4 font-medium">Role</th>
                <th className="py-2 pr-4 font-medium">Status</th>
                <th className="py-2 pr-4 font-medium">Last sign-in</th>
                {canManage && <th className="py-2 font-medium">Access</th>}
              </tr>
            </thead>
            <tbody className="divide-y">
              {users.data?.map((u) => (
                <tr key={u.id}>
                  <td className="py-3 pr-4">
                    <p className="font-medium">{u.full_name}</p>
                    <p className="text-xs text-muted-foreground">{u.email}</p>
                  </td>
                  <td className="py-3 pr-4">
                    {canManage ? (
                      <select
                        aria-label={`Role for ${u.full_name}`}
                        className={selectClass}
                        value={u.role}
                        onChange={(e) => update(u.id, { role: e.target.value }, `${u.full_name} is now ${e.target.value}.`)}
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <Badge>{u.role}</Badge>
                    )}
                  </td>
                  <td className="py-3 pr-4">
                    <div className="flex flex-wrap gap-1">
                      <Badge variant={u.is_active ? "default" : "destructive"}>{u.is_active ? "Active" : "Disabled"}</Badge>
                      {u.mfa_enabled && <Badge variant="neutral">2FA</Badge>}
                    </div>
                  </td>
                  <td className="py-3 pr-4 text-muted-foreground">{formatDate(u.last_login_at)}</td>
                  {canManage && (
                    <td className="py-3">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => update(u.id, { is_active: !u.is_active }, u.is_active ? `${u.full_name} was disabled.` : `${u.full_name} was re-enabled.`)}
                      >
                        {u.is_active ? "Disable" : "Enable"}
                      </Button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {canInvite && (
        <Card>
          <CardHeader>
            <CardTitle>Invite someone</CardTitle>
            <CardDescription>They receive a link that expires in 7 days.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <form
              className="flex flex-wrap items-end gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                void run(
                  () => api("invitations", { body: { email: inviteEmail, role: inviteRole } }),
                  `Invitation sent to ${inviteEmail}.`,
                  () => {
                    setInviteEmail("");
                    invites.reload();
                    seats.reload();
                  },
                );
              }}
            >
              <div className="min-w-[14rem] flex-1 space-y-2">
                <Label htmlFor="invite-email">Email</Label>
                <Input id="invite-email" type="email" value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} required />
              </div>
              <div className="space-y-2">
                <Label htmlFor="invite-role">Role</Label>
                <select id="invite-role" className={`${selectClass} h-10`} value={inviteRole} onChange={(e) => setInviteRole(e.target.value as Role)}>
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </div>
              <Button type="submit">Send invitation</Button>
            </form>
            {invites.data && invites.data.length > 0 && (
              <ul className="divide-y rounded-md border">
                {invites.data.map((i) => (
                  <li key={i.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                    <span>
                      {i.email} <Badge className="ml-2">{i.role}</Badge>
                      <span className="ml-2 text-xs text-muted-foreground">expires {formatDate(i.expires_at)}</span>
                    </span>
                    <Button variant="ghost" size="sm" onClick={() => run(() => api(`invitations/${i.id}`, { method: "DELETE" }), "Invitation revoked.", () => { invites.reload(); seats.reload(); })}>
                      Revoke
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}

      {me.permissions.includes("org:manage") && (
        <Card>
          <CardHeader>
            <CardTitle>Organization</CardTitle>
          </CardHeader>
          <CardContent>
            <form
              className="flex flex-wrap items-end gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                void run(() => api("organization", { method: "PATCH", body: { name: orgName } }), "Organization renamed.", () => window.location.reload());
              }}
            >
              <div className="min-w-[14rem] flex-1 space-y-2">
                <Label htmlFor="org-name">Name</Label>
                <Input id="org-name" value={orgName} onChange={(e) => setOrgName(e.target.value)} minLength={2} required />
              </div>
              <Button type="submit" variant="outline">
                Save name
              </Button>
            </form>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
