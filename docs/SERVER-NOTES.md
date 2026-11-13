# Server notes (handoff between chats)

Start a new chat with: **"Read docs/SERVER-NOTES.md first."** Update this file whenever something changes.
No passwords or keys go in here.

## The server

| | |
|---|---|
| Provider | Contabo VPS (host name `vmi3745291`), Ubuntu |
| IP address | `62.146.227.209` |
| Public address | `https://pullomsourceai.duckdns.org` (DuckDNS, free). HTTPS certificate issued 2026-11-12, valid to 2027-02-10 (auto-renews) |
| App folder | `/opt/copilot/app` (a git checkout of branch `ccr-b945e3ef-n46e9q`) |
| Settings file | `/opt/copilot/app/.env` (owned by user `deploy`; holds all secrets) |
| Runs as | Docker Compose, user `deploy` (`sudo -iu deploy`, then `cd /opt/copilot/app`) |
| Connected from | Termux on an Android phone |

## How to log in

From Termux, use the key file by name (the private key itself is never stored in this repo):

    ssh -i ~/.ssh/copilot_server2 root@62.146.227.209

Then `sudo -iu deploy` and `cd /opt/copilot/app`.

Optional shortcut, run once in Termux (not on the server):

    printf 'Host copilot\n  HostName 62.146.227.209\n  User root\n  IdentityFile ~/.ssh/copilot_server2\n' >> ~/.ssh/config

After that, `ssh copilot` logs in.

Never commit private keys (files without `.pub`) to this repo.

The server accepts **SSH keys only** (no passwords): `scripts/server-bootstrap.sh` turned password login off in
`/etc/ssh/sshd_config.d/10-copilot.conf`. The key the server knows is Termux's `~/.ssh/copilot_server2` (made
2026-11-11). A second key, `~/.ssh/id_ed25519` (made 2026-11-12), is NOT on the server; plain `ssh root@...` uses it
and gets "Permission denied (publickey)". If locked out, use the provider's console (Contabo control panel > VPS >
VNC), which does not depend on SSH.

Do app work as `deploy`, not `root` (root-owned files in `/opt/copilot/app` make later updates fail; fix with
`chown -R deploy:deploy /opt/copilot/app` as root). Never edit tracked files on the server (for example
`docker-compose.yml`): ask for a repo change instead, or the next `git checkout` refuses to run.

## Status (2026-11-13)

**Live:** https://pullomsourceai.duckdns.org with HTTPS (nginx + Let's Encrypt via `scripts/ssl-init.sh`).
`COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml`, `COMPOSE_PROFILES=local-llm,search`.

**Code:** branch `ccr-b945e3ef-n46e9q`. The latest commit to deploy is `ebfb9f6` (worker heartbeat on the shared
volume), which comes after `7f83a2f` (local AI timeouts). The server was still on `3137c29` on 2026-11-13. A
hand edit to `docker-compose.yml` (the worker heartbeat path) blocked the update; it was stashed and the same change
is now in the repo. Database at migration `0015`.

**Settings in effect (.env):**
- `REQUIRE_EMAIL_VERIFICATION=false` (no mail service yet), `PLATFORM_ADMIN_REQUIRES_MFA=true`.
- AI: local Ollama container, model `llama3.2:3b`, `LOCAL_LLM_URL=http://ollama:11434`,
  `AI_PROVIDER_ORDER=local,anthropic,openai`. No Anthropic/OpenAI key. Duplicate `LOCAL_LLM_*` lines were removed.
- Web search: self-hosted SearXNG, `SEARCH_PROVIDER=searxng`, `SEARXNG_URL=http://searxng:8080`.
- Server: 7.8 GB RAM + 4 GB swap; about 5.4 GB free with everything running.

**Open problem:** AI jobs (`requirements.extract`, `document.analyze`) failed with "local request timed out": the
CPU model is slower than the old 120 s per call / 300 s per job limits. Fixed in code (local calls: 900 s timeout,
16384-token context, document text capped at 24000 characters). To apply on the server, see step 1 below.

**Update 2026-11-13 (later):** a manifest upload then failed with "local returned HTTP 500" (Ollama itself
erroring; cause not yet known, likely memory). Code now shows Ollama's own reason in the error message and defaults
to a smaller window (`LOCAL_LLM_NUM_CTX=8192`, `LOCAL_LLM_MAX_INPUT_CHARS=12000`). After deploying, if it still
fails, the site's error message and `docker compose logs --tail=40 ollama` give the cause.

**Update 2026-11-13 (evening):** server updated to the local-AI fixes. Manifest analysis worked on the local
model (slow). Requirement extraction did not: one piece took over 15 minutes on `llama3.2:3b` and timed out.
Decision: hosted AI first, local as backup. `ANTHROPIC_API_KEY` is set (key named `copilot-server`, $5 monthly
limit in the console; check its expiry date there) and `AI_PROVIDER_ORDER=anthropic,local`; health shows
`anthropic, local`. `SUPPLIER_INVITES=review` is set. When the monthly limit is used up, the site falls back to
the slow local model. Five old jobs failed from the timeouts; retry them in Back office > Jobs.
Secrets are entered with the `read -rsp` line below: run it unchanged, then paste at the `Key:` prompt.

**Update 2026-11-13 (night):** with the key set, every Anthropic call failed with HTTP 400
(`tool_choice: type "tool" and "any" are not supported for this model`), so the app kept falling back to the slow
local model. Cause: `claude-sonnet-5-5` rejects forced tool calls and `temperature`. Fixed in code: the Anthropic
provider now uses JSON outputs (`output_config.format`) and sends `ANTHROPIC_EFFORT` (default `medium`). Not yet
verified against the live API (no key in the build sandbox): after deploying, check with
`select provider, status, left(error,140) from ai_audit_logs order by id desc limit 5;` in the `db` container.

**Stop button (2026-11-13):** queued or running jobs can be stopped from **Jobs** (needs the jobs:manage
permission) and from **Back office > Jobs**. A running job stops within about 3 seconds
(`JOB_CANCEL_POLL_SECONDS`), credits it still held are refunded, and its document or extraction is marked
stopped. Retry starts a stopped job again from the beginning (Stop + Retry is the pause/resume). Needs migration
`0016`, which runs automatically on deploy.

## Next steps, in order

1. Deploy the fix and retry the jobs (as `deploy` in `/opt/copilot/app`):
   ```
   git fetch origin ccr-b945e3ef-n46e9q && git checkout -B ccr-b945e3ef-n46e9q origin/ccr-b945e3ef-n46e9q
   git stash drop                      # only if the old docker-compose.yml edit is still stashed
   ./scripts/set-env.sh JOB_TIMEOUT_SECONDS 1800
   ./scripts/set-env.sh JOB_VISIBILITY_SECONDS 2400
   docker compose up -d --build --wait
   docker compose exec -T worker python -c "from app.config import get_settings as g; s=g(); print(s.local_llm_timeout_seconds, s.local_llm_num_ctx, s.job_timeout_seconds)"
   ```
   The last line must print `900.0 16384 1800`. Then retry the failed jobs in Back office > Jobs.
   If they still fail, read the errors:
   `docker compose exec -T db psql -U copilot -d copilot -c "select kind, status, attempts, left(last_error,200) from jobs where status in ('dead','running','queued') order by created_at desc limit 5;"`
2. Confirm these were done (recommended 2026-11-13, not yet confirmed):
   - `./scripts/set-env.sh SUPPLIER_INVITES review`. Mail is only captured by Mailpit, so "sent" invitations
     reach nobody but still start each supplier's 90-day cooldown.
   - `./scripts/set-env.sh SEARXNG_SECRET "$(openssl rand -hex 32)"` (it was the placeholder `change-me`).
   - `find /opt/copilot/app -user root | head` prints nothing (some setup was done as root).
3. Owner account: register at the site, turn on two-factor under Security, then
   `docker compose exec backend python -m app.ops grant-platform-admin YOUR@EMAIL`.
4. Share https://pullomsourceai.duckdns.org with the business partner.
5. Later:
   - Set up a mail service (`SMTP_*`), then set `REQUIRE_EMAIL_VERIFICATION=true` and `SUPPLIER_INVITES=auto`.
   - Optionally add an Anthropic key as a fallback; it is used automatically when the local model fails.
   - Add Stripe keys.

**Reading captured emails** (verification, resets): in a second Termux session run
`ssh -i ~/.ssh/copilot_server2 -N -L 8025:127.0.0.1:8025 root@62.146.227.209`, then open http://localhost:8025.
Never expose Mailpit publicly.

**Entering secrets** without showing them or saving them in shell history:
`read -rsp "Key: " K && ./scripts/set-env.sh NAME "$K"; unset K; echo`

## Handy commands (as `deploy` in `/opt/copilot/app`)

| | |
|---|---|
| What is running | `docker compose ps` |
| Health checks | `docker compose exec backend python -m app.ops health` |
| Update to the latest code | `git fetch origin ccr-b945e3ef-n46e9q && git checkout -B ccr-b945e3ef-n46e9q origin/ccr-b945e3ef-n46e9q && docker compose up -d --build --wait` |
| Change a setting | `./scripts/set-env.sh NAME value` then `docker compose up -d` |
| Back up now | `./scripts/backup.sh` |
| Logs | `docker compose logs --tail=50 backend` (or `worker`, `frontend`, `nginx`) |

Full reference: `docs/DEPLOYMENT.md` (server), `docs/TERMUX.md` (running on the phone itself).
