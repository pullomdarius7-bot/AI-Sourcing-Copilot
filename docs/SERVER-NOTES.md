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
`/etc/ssh/sshd_config.d/10-copilot.conf`. Termux's key is `~/.ssh/id_ed25519` (made 2026-11-12); it must be listed
in `/root/.ssh/authorized_keys` on the server. If locked out, use the provider's console (Contabo control panel >
VPS > VNC), which does not depend on SSH.

## Status (2026-11-12)

- **Live at https://pullomsourceai.duckdns.org** with HTTPS (nginx + Let's Encrypt, via `scripts/ssl-init.sh`).
- Code: branch `ccr-b945e3ef-n46e9q`, with back office, guest quotes and Termux scripts. Database at migration `0015`.
- Containers healthy. `docker-compose` uses `docker-compose.yml:docker-compose.prod.yml`; `COMPOSE_PROFILES` empty.
- Settings in effect: `REQUIRE_EMAIL_VERIFICATION=false` (no mail service yet), `PLATFORM_ADMIN_REQUIRES_MFA=true`.
- AI: local Ollama container (`COMPOSE_PROFILES=local-llm,search`), model `llama3.2:3b`,
  `LOCAL_LLM_URL=http://ollama:11434`, `AI_PROVIDER_ORDER=local,anthropic,openai` (no Anthropic/OpenAI key yet).
  Web search: self-hosted SearXNG (`SEARCH_PROVIDER=searxng`, `SEARXNG_URL=http://searxng:8080`). Health: both OK.
  Server has 7.8 GB RAM + 4 GB swap.
  Two AI jobs failed on 2026-11-13 with "local request timed out" (CPU model slower than the 120 s / 300 s limits).
  Fixed in code: local calls get LOCAL_LLM_TIMEOUT_SECONDS=900, LOCAL_LLM_NUM_CTX=16384, document text capped at
  LOCAL_LLM_MAX_INPUT_CHARS=24000; on the server also set JOB_TIMEOUT_SECONDS=1800 and JOB_VISIBILITY_SECONDS=2400.
- Some setup was done as `root` instead of `deploy`; check `find /opt/copilot/app -user root` is empty.
- Not set up yet: mail relay (`SMTP_*`; mail is captured in mailpit), Stripe keys, AI keys, web search key,
  `INVITE_SENDER_POSTAL_ADDRESS` (supplier invitations are held).
- Still to do: enable two-factor for the owner account, grant platform admin, send the link to the business partner.
- Termux login works with `ssh -i ~/.ssh/copilot_server2 root@62.146.227.209`.

## Next steps, in order

1. Owner account: register at the site, turn on two-factor under Security, then as `deploy` in `/opt/copilot/app`:
   `docker compose exec backend python -m app.ops grant-platform-admin YOUR@EMAIL`
2. Check the Back office **Health** tab; it lists anything still missing.
3. Share `https://pullomsourceai.duckdns.org` with the partner.
4. Later: mail relay, then set `REQUIRE_EMAIL_VERIFICATION=true`; Stripe, AI and search keys.

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
