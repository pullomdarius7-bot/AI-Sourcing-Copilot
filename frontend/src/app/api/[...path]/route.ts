import { NextRequest, NextResponse } from "next/server";

/**
 * Backend-for-frontend proxy.
 * - Keeps JWTs in httpOnly cookies so page scripts can never read them.
 * - Attaches the bearer token, transparently refreshes it once on 401.
 * - Rejects cross-origin state-changing requests.
 * - Passes JSON, multipart uploads and binary downloads through unchanged.
 */
export const dynamic = "force-dynamic";

const API = process.env.API_INTERNAL_URL ?? "http://backend:8000";
const SECURE = process.env.COOKIE_SECURE === "true";
const REFRESH_DAYS = Number(process.env.REFRESH_TOKEN_DAYS ?? "30");
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const TOKEN_PATHS = new Set(["auth/login", "auth/mfa/login"]);
const PASSTHROUGH_HEADERS = ["content-disposition", "content-security-policy", "x-content-type-options", "retry-after"];

type TokenBody = { access_token?: string; refresh_token?: string; expires_in?: number };

function cookieOptions(maxAge: number) {
  return { httpOnly: true, secure: SECURE, sameSite: "lax" as const, path: "/", maxAge };
}

function setTokens(res: NextResponse, t: TokenBody) {
  if (!t.access_token || !t.refresh_token) return;
  res.cookies.set("access_token", t.access_token, cookieOptions(t.expires_in ?? 900));
  res.cookies.set("refresh_token", t.refresh_token, cookieOptions(REFRESH_DAYS * 86400));
}

function clearTokens(res: NextResponse) {
  res.cookies.set("access_token", "", cookieOptions(0));
  res.cookies.set("refresh_token", "", cookieOptions(0));
}

async function forward(req: NextRequest, path: string, body: ArrayBuffer | undefined, access?: string) {
  const headers: Record<string, string> = {
    accept: req.headers.get("accept") ?? "application/json",
    "user-agent": req.headers.get("user-agent") ?? "",
    "x-forwarded-for": req.headers.get("x-forwarded-for") ?? "",
  };
  if (body !== undefined) headers["content-type"] = req.headers.get("content-type") ?? "application/json";
  if (access) headers.authorization = `Bearer ${access}`;
  // The export API is called by scripts with an API key instead of a browser session.
  const clientAuth = req.headers.get("authorization");
  if (path.startsWith("export/") && clientAuth?.startsWith("Bearer sca_")) headers.authorization = clientAuth;
  // Stripe signs the raw webhook body; the backend needs the signature to verify it.
  const stripeSignature = req.headers.get("stripe-signature");
  if (stripeSignature) headers["stripe-signature"] = stripeSignature;
  return fetch(`${API}/api/v1/${path}${req.nextUrl.search}`, {
    method: req.method,
    headers,
    body,
    cache: "no-store",
  });
}

async function handler(req: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join("/");

  if (!SAFE_METHODS.has(req.method)) {
    const origin = req.headers.get("origin");
    if (origin && new URL(origin).host !== req.headers.get("host")) {
      return NextResponse.json({ error: { code: "forbidden", message: "Cross-origin request blocked" } }, { status: 403 });
    }
  }
  if (path === "auth/refresh") {
    return NextResponse.json({ error: { code: "not_found", message: "Not found" } }, { status: 404 });
  }

  const body = SAFE_METHODS.has(req.method) ? undefined : await req.arrayBuffer();
  let access = req.cookies.get("access_token")?.value;
  const refresh = req.cookies.get("refresh_token")?.value;

  let upstream: Response;
  try {
    upstream = await forward(req, path, body, access);
  } catch {
    return NextResponse.json({ error: { code: "unavailable", message: "The service is unavailable" } }, { status: 502 });
  }

  let rotated: TokenBody | null = null;
  let sessionLost = false;
  if (upstream.status === 401 && refresh && !TOKEN_PATHS.has(path)) {
    const r = await fetch(`${API}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "content-type": "application/json", "user-agent": req.headers.get("user-agent") ?? "" },
      body: JSON.stringify({ refresh_token: refresh }),
      cache: "no-store",
    });
    if (r.ok) {
      rotated = (await r.json()) as TokenBody;
      access = rotated.access_token;
      upstream = await forward(req, path, body, access);
    } else {
      sessionLost = true;
    }
  }

  const buffer = await upstream.arrayBuffer();
  let payload: ArrayBuffer | string = buffer;
  let tokens: TokenBody | null = null;
  if (TOKEN_PATHS.has(path) && upstream.ok) {
    const data = JSON.parse(new TextDecoder().decode(buffer)) as TokenBody & Record<string, unknown>;
    if (data.access_token && data.refresh_token) {
      tokens = data;
      payload = JSON.stringify({ mfa_required: false });
    }
  }

  const res = new NextResponse(upstream.status === 204 ? null : payload, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
      "cache-control": "no-store",
    },
  });
  for (const name of PASSTHROUGH_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) res.headers.set(name, value);
  }

  if (tokens) setTokens(res, tokens);
  else if (rotated) setTokens(res, rotated);
  if (sessionLost || path === "auth/logout" || path === "auth/logout-all") clearTokens(res);
  if (path === "auth/password/reset" && upstream.ok) clearTokens(res);
  return res;
}

export { handler as GET, handler as POST, handler as PUT, handler as PATCH, handler as DELETE };
