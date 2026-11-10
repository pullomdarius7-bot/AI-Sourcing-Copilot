// Load test for the AI Sourcing Copilot (k6). Run it with scripts/loadtest.sh.
//
// It signs in once (the login endpoint is rate-limited per address), shares that session with every virtual user,
// and then mixes the read paths people use all day with a slow trickle of assistant questions (the assistant is
// limited to AGENT_RATE_PER_MINUTE per user). The whole run stays under the 15-minute access-token lifetime, so no
// token refresh happens mid-test.
//
// PROFILE=smoke   2 users for 1 minute           - is everything wired up?
// PROFILE=load    ramp to 25 users, 9 minutes    - expected busy-day traffic (default)
// PROFILE=stress  ramp to 100 users, 10 minutes  - find the knee; expect thresholds to fail somewhere
import http from "k6/http";
import { check, group, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const BASE = (__ENV.BASE_URL || "http://127.0.0.1:3000").replace(/\/$/, "");
const EMAIL = __ENV.LOADTEST_EMAIL;
const PASSWORD = __ENV.LOADTEST_PASSWORD;
const PROFILE = __ENV.PROFILE || "load";
const AGENT_PER_MINUTE = Number(__ENV.AGENT_PER_MINUTE || 10);

const readLatency = new Trend("copilot_read_duration", true);
const agentLatency = new Trend("copilot_agent_duration", true);
const errors = new Rate("copilot_errors");

const browseProfile = {
  smoke: { executor: "constant-vus", vus: 2, duration: "1m" },
  load: {
    executor: "ramping-vus",
    startVUs: 0,
    stages: [
      { duration: "2m", target: 25 },
      { duration: "6m", target: 25 },
      { duration: "1m", target: 0 },
    ],
  },
  stress: {
    executor: "ramping-vus",
    startVUs: 0,
    stages: [
      { duration: "2m", target: 25 },
      { duration: "2m", target: 50 },
      { duration: "2m", target: 100 },
      { duration: "3m", target: 100 },
      { duration: "1m", target: 0 },
    ],
  },
}[PROFILE];

export const options = {
  insecureSkipTLSVerify: __ENV.INSECURE_TLS === "1",
  scenarios: {
    browse: { ...browseProfile, exec: "browse" },
    assistant: {
      executor: "constant-arrival-rate",
      exec: "assistant",
      rate: AGENT_PER_MINUTE,
      timeUnit: "1m",
      duration: PROFILE === "smoke" ? "1m" : "9m",
      preAllocatedVUs: 2,
      maxVUs: 5,
    },
  },
  thresholds: {
    copilot_errors: ["rate<0.01"],
    copilot_read_duration: ["p(95)<800", "p(99)<2000"],
    copilot_agent_duration: ["p(95)<5000"],
    http_req_failed: ["rate<0.01"],
  },
};

export function setup() {
  if (!EMAIL || !PASSWORD) {
    throw new Error("Set LOADTEST_EMAIL and LOADTEST_PASSWORD (a user without two-factor login, e.g. role auditor).");
  }
  const health = http.get(`${BASE}/api/healthz`);
  if (health.status !== 200) throw new Error(`${BASE}/api/healthz answered ${health.status}`);
  const res = http.post(`${BASE}/api/auth/login`, JSON.stringify({ email: EMAIL, password: PASSWORD }), {
    headers: { "content-type": "application/json" },
  });
  if (res.status !== 200 || !res.cookies.access_token) {
    throw new Error(`Sign-in failed with status ${res.status}: ${String(res.body).slice(0, 200)}`);
  }
  return { access: res.cookies.access_token[0].value, refresh: res.cookies.refresh_token[0].value };
}

function session(data) {
  const jar = http.cookieJar();
  jar.set(BASE, "access_token", data.access);
  jar.set(BASE, "refresh_token", data.refresh);
}

function read(path, name) {
  const res = http.get(`${BASE}${path}`, { tags: { name } });
  readLatency.add(res.timings.duration);
  const ok = check(res, { [`${name} 200`]: (r) => r.status === 200 });
  errors.add(!ok);
  return res;
}

export function browse(data) {
  session(data);
  group("dashboard", () => {
    read("/api/auth/me", "me");
    read("/api/documents?limit=20", "documents");
  });
  sleep(1 + Math.random() * 2);
  group("sourcing", () => {
    const rfqs = read("/api/rfqs", "rfqs");
    read("/api/suppliers", "suppliers");
    let first = null;
    try {
      first = (rfqs.json("items") || [])[0];
    } catch (_) {
      first = null;
    }
    if (first && first.id) {
      read(`/api/rfqs/${first.id}`, "rfq detail");
      read(`/api/rfqs/${first.id}/quotes`, "rfq quotes");
      read(`/api/rfqs/${first.id}/comparisons`, "rfq comparisons");
    }
  });
  sleep(1 + Math.random() * 2);
  group("reference", () => {
    read("/api/fx-rates", "fx rates");
    read("/api/agent/status", "assistant status");
    read("/api/agent/conversations", "assistant conversations");
  });
  sleep(2 + Math.random() * 3);
}

const QUESTIONS = [
  "What is DAP?",
  "What should we do next?",
  "How does this application score quotes?",
  "What is the difference between FOB and CIF?",
  "How is risk scored?",
];

export function assistant(data) {
  session(data);
  const question = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
  const res = http.post(`${BASE}/api/agent/ask`, JSON.stringify({ question }), {
    headers: { "content-type": "application/json" },
    tags: { name: "assistant ask" },
    timeout: "120s",
  });
  agentLatency.add(res.timings.duration);
  const ok = check(res, {
    "assistant 201": (r) => r.status === 201,
    "assistant answer is traceable": (r) => r.status === 201 && !!r.json("assistant.run.id"),
  });
  errors.add(!ok);
}
