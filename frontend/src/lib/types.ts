export type Role = "requester" | "buyer" | "approver" | "admin" | "auditor";
export const ROLES: Role[] = ["requester", "buyer", "approver", "admin", "auditor"];

export interface Me {
  id: string;
  organization_id: string;
  organization_name: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  email_verified: boolean;
  mfa_enabled: boolean;
  last_login_at: string | null;
  created_at: string;
  permissions: string[];
}

export interface TeamUser {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  email_verified: boolean;
  mfa_enabled: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface Invitation {
  id: string;
  email: string;
  role: Role;
  expires_at: string;
  created_at: string;
}

export interface SessionInfo {
  id: string;
  user_agent: string | null;
  ip: string | null;
  created_at: string;
  last_used_at: string;
  expires_at: string;
  current: boolean;
}

export interface AuditEntry {
  id: number;
  actor_email: string | null;
  action: string;
  ip: string | null;
  meta: Record<string, unknown>;
  created_at: string;
}
