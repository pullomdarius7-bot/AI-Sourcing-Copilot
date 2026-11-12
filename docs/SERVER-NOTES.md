# Server notes (handoff between chats)

Start a new chat with: **"Read docs/SERVER-NOTES.md first."** Update this file whenever something changes.
No passwords or keys go in here.

## The server

| | |
|---|---|
| Provider | Contabo VPS (host name `vmi3745291`), Ubuntu |
| IP address | `62.146.227.209` |
| Public address (planned) | `https://pullomsourceai.duckdns.org` (DuckDNS, free) |
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

- Updated to the latest code (back office, guest quotes, Termux scripts); database migrated to `0015`; all
  containers healthy.
- Still in **private mode**: `DOMAIN=localhost`, no nginx, site only reachable through an SSH tunnel.
- `pullomsourceai.duckdns.org` returns an error because nothing answers on ports 80/443 yet.
- Locked out of SSH from Termux after logging out (the new Termux key is not on the server yet).

## Next steps, in order

1. Get back in (provider console), add Termux's key to `/root/.ssh/authorized_keys`, confirm `ssh root@62.146.227.209` works.
2. DuckDNS dashboard: `pullomsourceai` must point at `62.146.227.209`.
3. Go public (as `deploy` in `/opt/copilot/app`):
   ```
   ./scripts/configure-prod.sh pullomsourceai.duckdns.org YOUR@EMAIL
   ./scripts/set-env.sh COMPOSE_PROFILES ""
   ./scripts/set-env.sh REQUIRE_EMAIL_VERIFICATION false
   ./scripts/set-env.sh PLATFORM_ADMIN_REQUIRES_MFA true
   chmod +x infra/nginx/99-reload-certs.sh
   ./scripts/ssl-init.sh --placeholder
   docker compose up -d --wait
   ./scripts/ssl-init.sh
   ```
4. Open the site, register, turn on two-factor under Security, then
   `docker compose exec backend python -m app.ops grant-platform-admin YOUR@EMAIL`.
5. Share the link with the business partner.
6. Later: email service (SMTP), Stripe keys, AI key, web search key; turn `REQUIRE_EMAIL_VERIFICATION` back on
   once email works.

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
