# Deployment and operations runbook

Everything here runs from this repository and Docker Compose. Replace these three values wherever they appear:

```bash
export DOMAIN=copilot.example.com      # public host name of the application
export ACME_EMAIL=ops@example.com      # receives Let's Encrypt expiry notices
export SERVER=203.0.113.10             # public IP address of the server
```

| # | Section | # | Section |
| --- | --- | --- | --- |
| 1 | [Production deployment](#1-production-deployment-guide) | 11 | [Monitoring stack](#11-monitoring-stack) |
| 2 | [VPS](#2-vps-deployment-guide) | 12 | [Logging stack](#12-logging-stack) |
| 3 | [Ubuntu](#3-ubuntu-deployment-guide) | 13 | [Health checks](#13-health-checks) |
| 4 | [Docker](#4-docker-deployment-guide) | 14 | [Load testing](#14-load-testing-procedure) |
| 5 | [Reverse proxy](#5-reverse-proxy-configuration) | 15 | [Rollback](#15-rollback-procedure) |
| 6 | [Nginx](#6-nginx-configuration) | 16 | [Security checklist](#16-security-checklist) |
| 7 | [SSL](#7-ssl-configuration) | 17 | [Production readiness checklist](#17-production-readiness-checklist) |
| 8 | [GitHub Actions pipeline](#8-github-actions-deployment-pipeline) | 18 | [Disaster recovery](#18-disaster-recovery-procedures) |
| 9 | [Backup](#9-backup-strategy) | 19 | [Post-deployment validation](#19-post-deployment-validation) |
| 10 | [Restore](#10-restore-strategy) | 20 | [Fresh Ubuntu server, start to finish](#20-exact-commands-to-deploy-from-a-fresh-ubuntu-server) |

To launch from nothing, go straight to [section 20](#20-exact-commands-to-deploy-from-a-fresh-ubuntu-server).

---

## 1. Production deployment guide

### Layout on the server

| Path | Content |
| --- | --- |
| `/opt/copilot/app` | this repository (code, compose files, `.env`, `.deploy/` release records) |
| `/opt/copilot/backups` | local backup archives (`BACKUP_DIR`) |
| `/etc/copilot/backup.pass` | backup encryption passphrase (root:deploy, mode 640) |
| `/var/log/copilot/` | logs of the scheduled jobs (backup, health, maintenance) |
| Docker volumes `ai-sourcing-copilot_*` | all application data (section 4) |

### Files that make a deployment "production"

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | the application; every port except the proxy's is bound to 127.0.0.1 |
| `docker-compose.prod.yml` | nginx on 80/443, certbot renewal, memory ceilings |
| `.env` | all settings and secrets (mode 600). `COMPOSE_FILE` and `COMPOSE_PROFILES` in it make plain `docker compose …` commands use the production overlay and the monitoring/logging profiles |
| `scripts/configure-prod.sh` | writes the production settings into `.env` |
| `scripts/deploy.sh` | build → pre-deploy DB backup → migrate → start → validate → record release; rolls back automatically on failure |

### Release procedure (every release after the first)

```bash
ssh deploy@$SERVER
cd /opt/copilot/app
./scripts/deploy.sh main        # git checkout: fetch and release the head of main (or a tag / commit)
# or, when the code was uploaded as an archive and unpacked over this directory:
./scripts/deploy.sh
```

What it does, in order:

1. Refuses to run twice at once, checks free disk space (5 GB), adds any new required secrets to `.env`.
2. Builds `copilot-backend:<version>` and `copilot-frontend:<version>` (version = 12-character commit id, or `r<UTC timestamp>` without git).
3. Dumps the database to `BACKUP_DIR/copilot-<time>-db-pre-<version>.tar[.gpg]`.
4. Sets `APP_VERSION` in `.env` and runs `docker compose up -d --wait`: the `migrate` container applies migrations, then the API, workers and front end are replaced. Users see a few seconds of 502 during the swap.
5. Runs `scripts/validate.sh`. If the stack is unhealthy or validation fails, it switches back to the previous release's images and exits non-zero.
6. Records the release in `.deploy/history`, keeps a snapshot of the deployment files in `.deploy/releases/`, and prunes all but the last 5 releases (`KEEP_RELEASES`).

```bash
make releases      # what is deployed and what can be rolled back to
```

### Changing a setting

```bash
cd /opt/copilot/app
./scripts/set-env.sh WORKER_CONCURRENCY 4
docker compose up -d            # recreates only the containers whose configuration changed
./scripts/validate.sh
```

### Outgoing mail (required before real users sign up)

Out of the box all mail is captured by the bundled Mailpit and reaches nobody. Point the application at your relay:

```bash
./scripts/set-env.sh SMTP_HOST smtp.example.com
./scripts/set-env.sh SMTP_PORT 587
./scripts/set-env.sh SMTP_STARTTLS true
./scripts/set-env.sh SMTP_USER copilot@example.com
./scripts/set-env.sh SMTP_PASSWORD 'the-relay-password'
./scripts/set-env.sh EMAIL_FROM "AI Sourcing Copilot <no-reply@$DOMAIN>"
docker compose up -d
```

Until then, read captured mail (verification links, password resets, RFQ packages) through an SSH tunnel:

```bash
ssh -N -L 8025:127.0.0.1:8025 deploy@$SERVER     # then open http://localhost:8025
```

### First administrator and closing registration

1. Open `https://$DOMAIN/register`, create your organization and administrator, confirm the verification mail.
2. Enable two-factor login for the administrator under *Security*.
3. Invite colleagues from *Team* (invitations work with registration closed).
4. Stop strangers from creating organizations:

```bash
./scripts/set-env.sh NGINX_REGISTER_DEFAULT 0
docker compose up -d nginx
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://$DOMAIN/api/auth/register   # 403
```

To allow registration from specific addresses, add lines such as `203.0.113.10 1;` to `infra/nginx/register-allow.conf` and run `docker compose exec nginx nginx -s reload`.

---

## 2. VPS deployment guide

### Size

| | Minimum | Recommended |
| --- | --- | --- |
| vCPU / RAM / disk | 2 / 4 GB / 40 GB SSD | 4 / 8 GB / 80 GB SSD |
| Profiles | none (`./scripts/set-env.sh COMPOSE_PROFILES ""`) | `monitoring,logging` |
| Swap | 4 GB (`SWAP_GB=4`, the bootstrap default) | 4 GB |

ClamAV alone holds 1–2 GB of RAM. On a 4 GB server either keep the profiles off or set `./scripts/set-env.sh SCAN_BACKEND builtin` (static checks only; not recommended). A local LLM (`--profile local-llm`) needs its own sizing (8 GB+ RAM for an 8B model, far better with a GPU).

### Provider checklist

1. Image: **Ubuntu 24.04 LTS** (22.04 also works), x86_64 or arm64.
2. Add your SSH public key at creation, so `ssh root@$SERVER` works without a password.
3. Provider firewall / security group: inbound TCP **22, 80, 443** only.
4. DNS: `A` record `$DOMAIN → $SERVER`. Add an `AAAA` record only after `curl -6 http://$DOMAIN/` reaches the server; a dangling `AAAA` record makes certificate issuance fail.
5. Enable the provider's automatic snapshots as a second safety net (they do not replace section 9).

```bash
dig +short A $DOMAIN          # must print $SERVER before section 7 can issue a certificate
ssh root@$SERVER 'lsb_release -ds; nproc; free -h; df -h /'
```

Then continue with [section 20](#20-exact-commands-to-deploy-from-a-fresh-ubuntu-server).

---

## 3. Ubuntu deployment guide

`scripts/server-bootstrap.sh` prepares a fresh Ubuntu server. Run it once as root from the unpacked repository:

```bash
sudo DEPLOY_USER=deploy SSH_PORT=22 SWAP_GB=4 /opt/copilot/app/scripts/server-bootstrap.sh
```

| It does | Verify with |
| --- | --- |
| `apt-get upgrade`, base packages (git, ufw, fail2ban, rsync, gnupg, cron, unzip) | `apt list --upgradable` |
| Docker Engine + Compose plugin from Docker's apt repository | `docker --version && docker compose version` |
| `/etc/docker/daemon.json`: container logs capped at 5 × 20 MB, `live-restore` | `docker info --format '{{.LoggingDriver}} {{.LiveRestoreEnabled}}'` |
| User `deploy` in group `docker`, with root's `authorized_keys` | `id deploy` |
| `/opt/copilot`, `/opt/copilot/backups`, `/etc/copilot`, `/var/log/copilot` | `ls -ld /opt/copilot /etc/copilot` |
| Swap file and `vm.swappiness=10` | `swapon --show` |
| ufw: deny incoming except 22, 80, 443 | `ufw status verbose` |
| SSH: keys only, no root password login (`/etc/ssh/sshd_config.d/10-copilot.conf`) | `sshd -T \| grep -E 'passwordauthentication\|permitrootlogin'` |
| fail2ban jail for SSH | `fail2ban-client status sshd` |
| Unattended security updates, NTP | `systemctl is-active unattended-upgrades; timedatectl show -p NTPSynchronized` |

The script is safe to run again. If it prints `SKIPPED: no authorized_keys found`, add your public key to `/home/deploy/.ssh/authorized_keys` and run it again before closing your session.

Operating system maintenance:

```bash
sudo apt-get update && sudo apt-get -y upgrade        # monthly, or rely on unattended-upgrades for security fixes
[ -f /var/run/reboot-required ] && sudo reboot        # containers come back by themselves (restart: unless-stopped)
ssh deploy@$SERVER 'cd /opt/copilot/app && docker compose ps && ./scripts/validate.sh'   # after the reboot
```

Published container ports bypass ufw. That is why every service except nginx is bound to 127.0.0.1 in the compose files; `scripts/validate.sh` reports any port that listens on a public interface.

---

## 4. Docker deployment guide

### Compose files and profiles

```bash
grep -E '^(COMPOSE_FILE|COMPOSE_PROFILES|APP_VERSION)=' /opt/copilot/app/.env
# COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml
# COMPOSE_PROFILES=monitoring,logging
# APP_VERSION=3f9c2ab71d04
```

| Profile | Services | Published on |
| --- | --- | --- |
| (always) | db, migrate (one-shot), backend, worker, frontend, minio, clamav, mailpit | frontend 127.0.0.1:3000, backend 127.0.0.1:8000, MinIO console 127.0.0.1:9001, Mailpit 127.0.0.1:8025 |
| production overlay | nginx, certbot | **0.0.0.0:80, 0.0.0.0:443** |
| `monitoring` | prometheus, grafana | 127.0.0.1:9090, 127.0.0.1:3001 |
| `logging` | loki, alloy | none |
| `local-llm` | ollama | 127.0.0.1:11434 |
| `tls` | caddy (alternative to the nginx overlay) | 0.0.0.0:80, 0.0.0.0:443 |
| `test` | backend-test | none |

### Everyday commands (run in `/opt/copilot/app`)

```bash
docker compose ps                                  # state and health of every container
docker compose logs -f --tail=100 backend worker   # follow logs
docker compose restart worker                      # restart one service
docker compose up -d --scale worker=3 worker       # more background workers
docker compose exec db psql -U copilot -d copilot  # SQL shell (superuser: bypasses row-level security)
docker compose run --rm migrate alembic current    # database revision
docker compose top                                 # processes
docker stats --no-stream                           # CPU and memory per container
docker system df                                   # disk used by images, volumes, build cache
```

Never run `docker compose down -v` or `make reset` on production: `-v` deletes the data volumes.

### Volumes (all data lives here)

| Volume | Content | In backups |
| --- | --- | --- |
| `ai-sourcing-copilot_pgdata` | PostgreSQL | yes (`db.dump`) |
| `ai-sourcing-copilot_minio_data` | uploaded documents, RFQ packages | yes |
| `ai-sourcing-copilot_uploads` | local-disk document fallback | yes |
| `ai-sourcing-copilot_letsencrypt`, `_certbot_www` | certificates | no (re-issued by `ssl-init.sh`) |
| `ai-sourcing-copilot_clamav_data` | virus signatures | no (downloaded again) |
| `ai-sourcing-copilot_prometheus_data`, `_grafana_data`, `_loki_data`, `_alloy_data` | metrics, logs | no |
| `ai-sourcing-copilot_mailpit` | captured mail | no |

```bash
docker volume ls --filter name=ai-sourcing-copilot_
docker run --rm -v ai-sourcing-copilot_pgdata:/v:ro postgres:16-alpine du -sh /v     # size of one volume
```

### Updating third-party images

Pinned tags live in `docker-compose.yml` and `docker-compose.prod.yml`. To pick up patch releases of a pinned tag:

```bash
./scripts/backup.sh
docker compose pull db minio clamav mailpit nginx certbot prometheus grafana loki alloy
docker compose up -d --wait
./scripts/validate.sh
```

To move to a newer tag, change it in the compose file, commit, and release with `./scripts/deploy.sh`. A PostgreSQL **major** upgrade (16 → 17) is a dump and restore: `./scripts/backup.sh`, change the tag, `docker compose down`, `docker volume rm ai-sourcing-copilot_pgdata`, `./scripts/restore.sh <the backup>`.

### Housekeeping

Installed by `install-cron.sh` (weekly): `docker image prune -f` and `docker builder prune -f --filter until=168h`. Manually, when disk is tight:

```bash
docker image prune -a -f --filter until=720h    # also removes release images older than 30 days
```

---

## 5. Reverse proxy configuration

```
Internet ──443/80──> nginx (container) ──> frontend:3000 (Next.js; holds the session cookies)
                                              └──> backend:8000 (FastAPI; never published publicly)
```

- Only nginx is reachable from outside. The API has no public route of its own; the browser only ever talks to `/api/*` on the front end, which forwards to the API inside the compose network.
- nginx overwrites `X-Forwarded-For` with the client's address. The application's login rate limit and audit log use it, so it must not be client-controlled.
- `/metrics` exists only on the API container (127.0.0.1:8000) and needs a token; it is not routed through the proxy.

Two interchangeable proxies ship with the repository. Use one.

| | nginx overlay (default in this runbook) | Caddy profile |
| --- | --- | --- |
| Enable | `./scripts/configure-prod.sh $DOMAIN $ACME_EMAIL` | `./scripts/set-env.sh COMPOSE_FILE docker-compose.yml` and `./scripts/set-env.sh COMPOSE_PROFILES tls,monitoring,logging` |
| Certificates | certbot container, `./scripts/ssl-init.sh` | automatic, nothing to run |
| Rate limits, body size, registration allow-list | yes (`infra/nginx/`) | no |
| Config | `infra/nginx/nginx.conf`, `infra/nginx/templates/copilot.conf.template` | `infra/caddy/Caddyfile` |

Caddy variant, complete:

```bash
cd /opt/copilot/app
./scripts/set-env.sh DOMAIN $DOMAIN
./scripts/set-env.sh APP_BASE_URL https://$DOMAIN
./scripts/set-env.sh COOKIE_SECURE true
./scripts/set-env.sh COMPOSE_FILE docker-compose.yml
./scripts/set-env.sh COMPOSE_PROFILES tls,monitoring,logging
./scripts/deploy.sh
```

A third option, nginx installed on the host instead of in a container, is in `infra/nginx/host/copilot.conf`; the install commands are at the top of that file.

---

## 6. Nginx configuration

| File | Mounted at | Content |
| --- | --- | --- |
| `infra/nginx/nginx.conf` | `/etc/nginx/nginx.conf` | workers, JSON access log, gzip, rate-limit zones |
| `infra/nginx/templates/copilot.conf.template` | rendered to `/etc/nginx/conf.d/copilot.conf` at start | the site: redirect, TLS, headers, routes |
| `infra/nginx/register-allow.conf` | `/etc/nginx/register-allow.conf` | addresses allowed to register when registration is closed |
| `infra/nginx/99-reload-certs.sh` | `/docker-entrypoint.d/` | reloads nginx every 12 h so renewed certificates are served |

Settings (in `.env`, applied with `docker compose up -d nginx`):

| Setting | Default | Meaning |
| --- | --- | --- |
| `DOMAIN` | – | server name and certificate path |
| `NGINX_CLIENT_MAX_BODY` | `25m` | largest request body; keep above `MAX_UPLOAD_MB` |
| `NGINX_REGISTER_DEFAULT` | `1` | `0` closes `/api/auth/register` except for `register-allow.conf` |

What the site configuration enforces:

| Route | Limit per client address | Timeout |
| --- | --- | --- |
| `/api/auth/*` | 5 requests/s, burst 20 (register: burst 10) | 120 s |
| `/api/agent/*` | 30 requests/s, burst 30 | 180 s |
| `/api/*` | 30 requests/s, burst 60 | 120 s |
| everything | 40 concurrent connections | 120 s |

Over the limit nginx answers 429. Headers on every response: `Strict-Transport-Security` (1 year, subdomains), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, `X-Request-Id`. The `Server` header carries no version.

Operate it:

```bash
docker compose exec nginx nginx -t                 # test the rendered configuration
docker compose exec nginx nginx -T | less          # show the rendered configuration
docker compose exec nginx nginx -s reload          # reload after editing register-allow.conf
docker compose up -d nginx                         # after editing nginx.conf, the template or .env
docker compose logs --tail=50 nginx                # JSON access log and errors
docker compose logs nginx | grep '"status":5'      # server errors seen by the proxy
```

---

## 7. SSL configuration

Certificates come from Let's Encrypt over HTTP-01 (port 80 must be reachable and `$DOMAIN` must resolve to the server).

```bash
cd /opt/copilot/app
./scripts/ssl-init.sh               # first certificate; deploy.sh also runs this
ACME_STAGING=1 ./scripts/ssl-init.sh   # rehearsal against the staging CA (untrusted certificate, no rate limits)
```

How it works: nginx cannot start without certificate files, so a two-day self-signed placeholder is created first; nginx starts; certbot proves control of the domain through `/.well-known/acme-challenge/`; the placeholder is replaced and nginx reloaded. If issuance fails the placeholder is restored, the site stays up, and the script tells you why.

Renewal is automatic: the `certbot` container runs `certbot renew` every 12 hours (it acts 30 days before expiry) and nginx reloads every 12 hours.

```bash
docker compose run --rm --entrypoint certbot certbot certificates                                   # what is installed, expiry
docker compose run --rm --entrypoint certbot certbot renew --webroot -w /var/www/certbot --dry-run   # prove renewal works
docker compose logs --tail=20 certbot
echo | openssl s_client -connect $DOMAIN:443 -servername $DOMAIN 2>/dev/null | openssl x509 -noout -issuer -dates
curl -sI https://$DOMAIN/login | grep -i strict-transport-security
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://$DOMAIN/                             # 301 https://…
```

Force a renewal (for example after a key compromise):

```bash
docker compose run --rm --entrypoint certbot certbot renew --webroot -w /var/www/certbot --force-renewal
docker compose exec nginx nginx -s reload
```

Moving from a staging certificate to a real one, or starting over:

```bash
docker compose run --rm --entrypoint sh certbot -c "rm -rf /etc/letsencrypt/live/$DOMAIN /etc/letsencrypt/archive/$DOMAIN /etc/letsencrypt/renewal/$DOMAIN.conf"
./scripts/ssl-init.sh
```

TLS settings (in the template): TLS 1.2 and 1.3 only, ECDHE AEAD cipher suites, no session tickets, HTTP/2. `COOKIE_SECURE=true` (set by `configure-prod.sh`) marks the session cookies `Secure`.

---

## 8. GitHub Actions deployment pipeline

Two workflows:

| Workflow | Trigger | Does |
| --- | --- | --- |
| `.github/workflows/ci.yml` | push to `main`, pull requests | lint, backend tests against PostgreSQL, front-end lint/typecheck/build, full stack up + smoke test, production overlay + nginx configuration test, monitoring check |
| `.github/workflows/deploy.yml` | after CI succeeds on `main`; or manually | SSH to the server and run `deploy <commit>`; manual runs can also `rollback [version]` or show `status` |

The server builds the images itself from the commit; no container registry is involved. The CI key is restricted on the server to `scripts/ci-entry.sh`, which accepts only `deploy <ref>`, `rollback [version]` and `status`.

### One-time setup

The server directory must be a git clone (section 20, variant B). For a private repository, give the server read access first:

```bash
# on the server, as deploy
ssh-keygen -t ed25519 -N "" -C "copilot-server" -f ~/.ssh/github_deploy
cat ~/.ssh/github_deploy.pub        # add at GitHub: repository → Settings → Deploy keys (read-only)
printf 'Host github.com\n  IdentityFile ~/.ssh/github_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
ssh-keyscan github.com >> ~/.ssh/known_hosts
cd /opt/copilot/app && git fetch origin && git status
```

Then create the CI key and register it (on your workstation, with the GitHub CLI authenticated for the repository):

```bash
ssh-keygen -t ed25519 -N "" -C "github-actions" -f ./copilot_ci
ssh-keyscan -p 22 $SERVER > ./copilot_known_hosts

# install the public key on the server, restricted to the deployment entry point
printf 'command="/opt/copilot/app/scripts/ci-entry.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty %s\n' "$(cat ./copilot_ci.pub)" \
  | ssh deploy@$SERVER 'cat >> ~/.ssh/authorized_keys'

gh api -X PUT "repos/{owner}/{repo}/environments/production" >/dev/null
gh secret set DEPLOY_SSH_KEY --env production < ./copilot_ci
gh secret set DEPLOY_KNOWN_HOSTS --env production < ./copilot_known_hosts
gh variable set DEPLOY_HOST --env production --body "$SERVER"
gh variable set DEPLOY_USER --env production --body "deploy"
gh variable set DEPLOY_PORT --env production --body "22"
gh variable set PRODUCTION_URL --env production --body "https://$DOMAIN"
shred -u ./copilot_ci && rm ./copilot_ci.pub ./copilot_known_hosts

ssh deploy@$SERVER 'tail -n1 ~/.ssh/authorized_keys | cut -c1-70'      # confirm the entry starts with command="…ci-entry.sh"
```

To require a human approval before each production release: GitHub → Settings → Environments → production → Required reviewers.

### Use

```bash
git push origin main                                         # CI runs; on success the commit is deployed
gh run watch                                                 # follow it
gh workflow run deploy.yml -f action=deploy -f target=v1.4.0 # deploy a tag, branch or commit on demand
gh workflow run deploy.yml -f action=rollback                # back to the previous release (database untouched)
gh workflow run deploy.yml -f action=rollback -f target=3f9c2ab71d04
gh workflow run deploy.yml -f action=status
```

A failed deployment leaves the previous release running (automatic rollback in `deploy.sh`) and fails the workflow run; the log shows the validation output and the last container logs.

---

## 9. Backup strategy

| What | How | When | Kept |
| --- | --- | --- | --- |
| PostgreSQL (all tenants, schema, grants, row-level security policies) | `pg_dump -Fc` → `db.dump` | hourly (`--db-only --label hourly`), daily (full), before every deployment and schema downgrade | hourly: 2 days; others: `BACKUP_KEEP_DAYS` (14) |
| Documents (MinIO volume + local fallback volume) | `tar` of the volumes, taken after the dump | daily (full) | 14 days |
| `.env` (JWT secret, **MFA encryption key**, passwords) and release history | copied into the full archive | daily (full) | 14 days |
| Off-site copy | `rsync` over SSH to `BACKUP_REMOTE` after every backup | every backup | set retention on the remote host |
| Restore test | `scripts/verify-backup.sh`: newest full backup into a scratch PostgreSQL | weekly (Sunday 04:30) | result in `BACKUP_DIR/verify.log` |

Recovery point: at most 1 hour of database changes; documents uploaded since the last full backup (up to 24 hours) exist only on the server until the next full backup. Run `./scripts/backup.sh` more often (for example every 6 hours in `/etc/cron.d/copilot`) if that window is too large.

Archives are named `copilot-<UTC time>[-db][-label].tar[.gpg]`, carry a `SHA256SUMS` manifest, and are encrypted with GPG AES-256 when `BACKUP_PASSPHRASE_FILE` is set. **Store a copy of the passphrase somewhere that is not this server** (password manager); without it the encrypted backups are useless. A full backup without encryption contains every secret in clear text.

### Set up

```bash
# as root: encryption passphrase readable by the deploy user only
sudo sh -c 'umask 027; openssl rand -base64 48 > /etc/copilot/backup.pass; chown root:deploy /etc/copilot/backup.pass'
sudo cat /etc/copilot/backup.pass          # copy it into your password manager now

# as deploy
cd /opt/copilot/app
./scripts/set-env.sh BACKUP_PASSPHRASE_FILE /etc/copilot/backup.pass
./scripts/set-env.sh BACKUP_KEEP_DAYS 14

# off-site copy to another machine you control (any host with SSH and rsync)
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
ssh-copy-id backup@backup.example.com
ssh-keyscan backup.example.com >> ~/.ssh/known_hosts
ssh backup@backup.example.com 'mkdir -p /srv/backups/copilot'
./scripts/set-env.sh BACKUP_REMOTE backup@backup.example.com:/srv/backups/copilot/

# schedule (as root)
sudo /opt/copilot/app/scripts/install-cron.sh deploy
```

### Run and check

```bash
./scripts/backup.sh                 # full backup now
./scripts/backup.sh --db-only       # database only
./scripts/verify-backup.sh          # restore test of the newest full backup
ls -lh /opt/copilot/backups | tail
tail -n 20 /var/log/copilot/backup.log
tail -n 3 /opt/copilot/backups/verify.log
ssh backup@backup.example.com 'ls -lh /srv/backups/copilot | tail -n 5'
```

`scripts/healthcheck.sh` (every 5 minutes) reports when the last full backup is older than 30 hours, and `scripts/validate.sh` warns after 26.

Not backed up, by design: certificates (re-issued), virus signatures (re-downloaded), metrics and logs (operational history only), captured mail.

---

## 10. Restore strategy

`scripts/restore.sh` verifies the archive's checksums, asks for confirmation, stops the application, recreates the database from the dump (including the restricted `copilot_app` role with the password from `.env`), restores the document volumes, starts the stack (pending migrations are applied, so an older backup works with newer code) and runs the validation.

```bash
cd /opt/copilot/app
ls -lh /opt/copilot/backups                                   # choose an archive

./scripts/restore.sh /opt/copilot/backups/copilot-20261110T021500Z.tar.gpg                 # database + documents
./scripts/restore.sh /opt/copilot/backups/copilot-20261110T130500Z-db-hourly.tar.gpg       # database only (hourly or pre-deploy archive)
./scripts/restore.sh /opt/copilot/backups/copilot-20261110T021500Z.tar.gpg --db-only       # only the database out of a full backup
./scripts/restore.sh /opt/copilot/backups/copilot-20261110T021500Z.tar.gpg --files-only    # only the documents
./scripts/restore.sh /opt/copilot/backups/copilot-20261110T021500Z.tar.gpg --with-env      # also take the backup's .env
./scripts/restore.sh <archive> --yes                                                       # no confirmation prompt (automation)
```

Use absolute paths. Add `--yes` only in scripts.

| Situation | Command |
| --- | --- |
| Bad data change an hour ago | `restore.sh <latest good hourly archive>` |
| A release damaged data | `restore.sh <copilot-…-db-pre-<version> archive>`, then `rollback.sh <previous version> --keep-db` |
| Document store lost or corrupted | `restore.sh <latest full archive> --files-only` |
| New server | section 18, scenario A |

Important details:

- The `.env` on the server must have the same `MFA_ENCRYPTION_KEY` as when the backup was made, or users with two-factor login cannot sign in. The script warns when they differ; `--with-env` installs the backup's `.env` (the current one is kept as `.env.before-restore.<time>`). On a server without `.env`, the backup's is installed automatically.
- After a database-only restore to an earlier point, documents uploaded after that point remain in the store without database rows (harmless), and rows never point at missing files.
- Fetching an archive from the off-site host: `rsync -a backup@backup.example.com:/srv/backups/copilot/copilot-20261110T021500Z.tar.gpg /opt/copilot/backups/`
- Decrypting by hand: `gpg --batch --pinentry-mode loopback --passphrase-file /etc/copilot/backup.pass -o backup.tar -d backup.tar.gpg && tar xf backup.tar`

Looking inside a backup without touching production (single record or tenant recovery):

```bash
mkdir -p /var/tmp/inspect && cd /var/tmp/inspect
gpg --batch --pinentry-mode loopback --passphrase-file /etc/copilot/backup.pass -o b.tar -d /opt/copilot/backups/copilot-20261110T021500Z.tar.gpg && tar xf b.tar
docker run -d --name copilot-scratch -e POSTGRES_PASSWORD=scratch postgres:16-alpine && sleep 8
docker exec copilot-scratch psql -U postgres -c 'CREATE ROLE copilot_app' -c 'CREATE ROLE copilot' -c 'CREATE DATABASE scratch'
docker exec -i copilot-scratch pg_restore -U postgres -d scratch --no-owner < copilot-*/db.dump
docker exec -it copilot-scratch psql -U postgres -d scratch        # query, or \copy rows out
docker rm -f copilot-scratch && cd / && rm -rf /var/tmp/inspect
```

---

## 11. Monitoring stack

Prometheus and Grafana run from the same compose project (profile `monitoring`, on by default after `configure-prod.sh`). Details of every metric and alert: `docs/MONITORING.md`.

```bash
cd /opt/copilot/app
grep '^COMPOSE_PROFILES=' .env                 # must contain "monitoring"
docker compose up -d --wait prometheus grafana
./scripts/check-monitoring.sh                  # Prometheus scrapes the API, rules loaded, dashboard provisioned
```

Both listen on 127.0.0.1 only. Reach them through an SSH tunnel from your workstation:

```bash
ssh -N -L 3001:127.0.0.1:3001 -L 9090:127.0.0.1:9090 deploy@$SERVER
# Grafana    http://localhost:3001   user admin, password:
ssh deploy@$SERVER "sed -n 's/^GRAFANA_ADMIN_PASSWORD=//p' /opt/copilot/app/.env"
# Prometheus http://localhost:9090/alerts
```

| Piece | Where |
| --- | --- |
| Scrape configuration (API `/metrics` with `METRICS_TOKEN`, every 15 s) | `infra/monitoring/prometheus/prometheus.yml` |
| Alert rules (API down, database down, 5xx rate, latency, job backlog, dead-lettered jobs, assistant errors/rejections/latency) | `infra/monitoring/prometheus/alerts.yml` |
| Dashboards *AI Sourcing Copilot* and *AI Sourcing Copilot - Logs* | `infra/monitoring/grafana/dashboards/` |
| Retention | `PROMETHEUS_RETENTION` (15d) |

```bash
curl -s 'http://127.0.0.1:9090/api/v1/alerts' | grep -o '"alertname":"[^"]*","[^}]*"state":"[a-z]*"' || echo "no alerts firing"
curl -s 'http://127.0.0.1:9090/api/v1/query?query=copilot_jobs_oldest_queued_seconds'
curl -s -H "Authorization: Bearer $(sed -n 's/^METRICS_TOKEN=//p' .env)" http://127.0.0.1:8000/metrics | grep '^copilot_' | head -n 20
docker compose exec prometheus promtool check rules /etc/prometheus/alerts.yml      # after editing the rules
curl -s -X POST http://127.0.0.1:9090/-/reload                                      # apply edited rules without a restart
```

Notifications: Prometheus evaluates the rules and shows them under `/alerts` and in Grafana; it does not send messages by itself. Out-of-band alerting that needs nothing else is the cron health probe: set a webhook that accepts `{"text": "..."}` (a self-hosted ntfy or Mattermost, for example) and you get one message per incident.

```bash
./scripts/set-env.sh ALERT_WEBHOOK_URL https://ntfy.example.com/copilot-alerts
```

Also watch from outside the server (any uptime monitor): `GET https://$DOMAIN/healthz` must answer 200 with `{"status":"ok"}`.

---

## 12. Logging stack

| Layer | Where | Retention |
| --- | --- | --- |
| Container stdout/stderr (API, workers, front end, nginx JSON access log, PostgreSQL, …) | Docker json-file driver | 5 × 20 MB per container (`/etc/docker/daemon.json`) |
| Central, searchable logs | Loki, shipped by Grafana Alloy from the Docker socket (profile `logging`) | 30 days (`infra/logging/loki.yml`, `retention_period`) |
| Scheduled jobs (backup, health, maintenance) | `/var/log/copilot/*.log` | 8 weekly rotations (`/etc/logrotate.d/copilot`) |
| Business audit trail (who did what, every assistant answer) | PostgreSQL `audit_logs`; in the app under *Audit log* | permanent, in backups |
| SSH and system | journald, `/var/log/auth.log` | Ubuntu defaults |

```bash
cd /opt/copilot/app
grep '^COMPOSE_PROFILES=' .env          # must contain "logging"
docker compose up -d loki alloy
docker compose logs --tail=20 alloy     # no error lines
```

Read logs directly:

```bash
docker compose logs -f --tail=200 backend
docker compose logs --since 30m worker | grep -Ei 'error|dead'
docker compose logs --since 1h nginx | grep '"status":5'
tail -f /var/log/copilot/health.log
journalctl -t copilot-health --since today
```

Read them in Grafana (tunnel from section 11): dashboard **AI Sourcing Copilot - Logs**, or *Explore* → data source *Loki*:

```logql
{job="copilot", service="backend", level=~"ERROR|CRITICAL"}
{job="copilot", service="worker"} |= "dead"
{job="copilot", service="nginx"} | json | status >= 500
{job="copilot", service="nginx"} | json | request_time > 2
sum by (service) (count_over_time({job="copilot", level=~"ERROR|CRITICAL"}[5m]))
{job="copilot"} |= "3f1c9a52-…"          # follow one X-Request-Id through nginx
```

Labels: `service` (compose service), `container`, `level` (when the line has one). Change retention by editing `retention_period` in `infra/logging/loki.yml` and running `docker compose up -d loki`.

Alloy reads container logs through the Docker socket, which gives that container read access to the Docker API. If that is not acceptable in your environment, remove `logging` from `COMPOSE_PROFILES` and rely on `docker compose logs`.

---

## 13. Health checks

| Check | Command | Healthy |
| --- | --- | --- |
| Public, through nginx | `curl -fsS https://$DOMAIN/healthz` | `{"status":"ok"}` |
| Front end | `curl -fsS http://127.0.0.1:3000/api/healthz` | `{"status":"ok"}` |
| API liveness | `curl -fsS http://127.0.0.1:8000/api/v1/health` | `{"status":"ok"}` |
| API readiness (database reachable) | `curl -fsS http://127.0.0.1:8000/api/v1/ready` | `{"status":"ready"}`; 503 when the database is down |
| PostgreSQL | `docker compose exec db pg_isready -U copilot -d copilot` | `accepting connections` |
| Document store | `docker compose exec minio mc ready local` | `The cluster is ready` |
| Every container | `docker compose ps` | `running`, and `(healthy)` where a check exists |
| One container's health history | `docker inspect --format '{{json .State.Health}}' $(docker compose ps -q backend)` | `"Status":"healthy"` |
| Background workers | `curl -s -H "Authorization: Bearer $(sed -n 's/^METRICS_TOKEN=//p' .env)" http://127.0.0.1:8000/metrics \| grep copilot_jobs_oldest_queued_seconds` | below 900 |
| nginx configuration | `docker compose exec nginx nginx -t` | `syntax is ok` |
| Certificate | `echo \| openssl s_client -connect $DOMAIN:443 -servername $DOMAIN 2>/dev/null \| openssl x509 -noout -checkend 1209600` | `Certificate will not expire` |
| Everything at once | `./scripts/validate.sh` | `VALIDATION PASSED` |

Built-in container health checks (Docker restarts nothing by itself on `unhealthy`, but `docker compose up --wait`, the deploy script and the probes below act on them): db (`pg_isready`), backend (`/api/v1/health`), frontend (`/api/healthz`), minio (`mc ready`), nginx (`/nginx-health` on an internal port), clamav (image built-in).

Automatic probing: `/etc/cron.d/copilot` runs `scripts/healthcheck.sh` every 5 minutes. It checks the public health endpoint, API readiness, that db/backend/worker/frontend are running, disk usage below 90 % and backup age; failures go to syslog (`journalctl -t copilot-health`), `/var/log/copilot/health.log` and the optional webhook.

```bash
./scripts/healthcheck.sh; echo "exit code $?"
```

First response when something is unhealthy:

```bash
docker compose ps
docker compose logs --tail=100 <service>
docker compose restart <service>            # backend, worker, frontend and nginx are safe to restart at any time
df -h / && docker system df                 # a full disk is the most common cause
```

---

## 14. Load testing procedure

The test is `loadtest/k6.js`, run in a container by `scripts/loadtest.sh`. It signs in once, then mixes the read paths (session, documents, RFQs, suppliers, RFQ detail/quotes/comparisons, exchange rates, assistant status) with assistant questions at 10 per minute.

| Profile | Shape | Use |
| --- | --- | --- |
| `smoke` | 2 users, 1 minute | after every deployment |
| `load` | ramp to 25 users, 9 minutes | before go-live and after sizing changes |
| `stress` | ramp to 100 users, 10 minutes | find the limit; run off-hours |

Pass criteria (k6 thresholds; the run exits non-zero when one fails): errors below 1 %, read p95 below 800 ms and p99 below 2 s, assistant p95 below 5 s (deterministic mode).

### Prepare (once)

1. In the app: *Team* → invite `loadtest@your-domain` with role **auditor** (read-only), accept the invitation, set a password, do **not** enable two-factor login.
2. Have realistic data in that organization (at least one RFQ with quotes), otherwise list endpoints return empty pages and flatter the result.

### Run

```bash
cd /opt/copilot/app
export LOADTEST_EMAIL=loadtest@example.com LOADTEST_PASSWORD='the-password'

./scripts/loadtest.sh smoke
./scripts/loadtest.sh load
./scripts/loadtest.sh stress
ls loadtest/results/                      # JSON summary per run
```

By default the target is `http://127.0.0.1:3000` on the server: the application itself, bypassing nginx and its per-address limit of 30 requests/s. To include the proxy and the network, run from another machine with Docker:

```bash
BASE_URL=https://$DOMAIN ./scripts/loadtest.sh smoke      # stays under the proxy's rate limits
```

A `load` or `stress` run from one address through nginx hits the 429 limit by design; that is the limit doing its job, not a capacity result.

### Watch while it runs (second terminal)

```bash
docker stats                                             # CPU and memory per container
docker compose logs -f --tail=0 backend | grep -E ' 5[0-9]{2} |ERROR'
docker compose exec db psql -U copilot -d copilot -c "SELECT state, count(*) FROM pg_stat_activity GROUP BY 1"
```

and the Grafana dashboard (latency p95, 5xx ratio, assistant answer time).

### Read the result

```bash
python3 - <<'EOF'
import json, glob
s = json.load(open(sorted(glob.glob("loadtest/results/*.json"))[-1]))["metrics"]
for name in ("copilot_read_duration", "copilot_agent_duration", "http_req_duration"):
    m = s[name]; print(f"{name:26} p95={m['p(95)']:.0f} ms  max={m['max']:.0f} ms")
print(f"errors={s['copilot_errors']['value']:.2%}  requests/s={s['http_reqs']['rate']:.1f}")
EOF
```

| Symptom | Look at | Lever |
| --- | --- | --- |
| API CPU at its ceiling, latency climbing | `docker stats` backend | more vCPUs; the API runs 2 worker processes per container |
| `backend` restarts, `OOMKilled` | `docker inspect --format '{{.State.OOMKilled}}' $(docker compose ps -q backend)` | `./scripts/set-env.sh BACKEND_MEMORY_LIMIT 3g` and `docker compose up -d` |
| Many `idle in transaction` / connection errors | `pg_stat_activity` | PostgreSQL defaults to 100 connections; each API process uses up to 30 |
| Document processing falls behind during uploads | `copilot_jobs_oldest_queued_seconds` | `docker compose up -d --scale worker=3 worker` or `WORKER_CONCURRENCY` |
| Assistant slow in model mode | run trace in the app | provider latency; `./scripts/set-env.sh AGENT_MODE deterministic` |

Afterwards, the test's assistant conversations belong to the load-test user and can be archived from the *Assistant* page.

---

## 15. Rollback procedure

Images and deployment files of the last 5 releases stay on the server, so a rollback rebuilds nothing and takes about a minute.

```bash
cd /opt/copilot/app
./scripts/rollback.sh --list                          # releases, database revision of each, which one is running
./scripts/rollback.sh                                 # previous release
./scripts/rollback.sh 3f9c2ab71d04 --keep-db          # a specific release, database untouched
./scripts/rollback.sh 3f9c2ab71d04 --with-db          # also downgrade the schema to that release's revision
gh workflow run deploy.yml -f action=rollback         # the same, from GitHub (always --keep-db)
```

Decide by what the bad release did to the database:

| The release … | Do |
| --- | --- |
| had no migration (`--list` shows the same revision) | `./scripts/rollback.sh` |
| added tables or columns only (the normal case) | `./scripts/rollback.sh <version> --keep-db` – older code ignores the additions |
| changed or removed something the older code needs | `./scripts/rollback.sh <version> --with-db` – a backup is taken first, the application is stopped, the schema is downgraded with the newer release's migrations, then the older release starts. Data in tables and columns added since is lost |
| wrote bad data | restore the pre-deploy backup, then roll the code back: `./scripts/restore.sh /opt/copilot/backups/copilot-<time>-db-pre-<bad version>.tar.gpg` and `./scripts/rollback.sh <previous version> --keep-db` (data entered after that backup is lost) |

When the database revisions differ and you pass neither `--keep-db` nor `--with-db`, the script stops and says so. `deploy.sh` rolls back by itself (`--keep-db`) when a new release does not become healthy or fails validation.

After any rollback:

```bash
./scripts/validate.sh
./scripts/rollback.sh --list
docker compose logs --tail=50 backend
```

If the images of the wanted release were already pruned: `./scripts/deploy.sh <that git ref>` builds and releases it again.

Emergency stop (take the application offline but keep data services up):

```bash
docker compose stop nginx frontend backend worker
docker compose start backend worker frontend nginx     # back online
```

---

## 16. Security checklist

Run on the server unless noted. Every line has a command that proves it.

**Host**

- [ ] Only 22, 80, 443 reachable from outside — from your workstation: `nmap -Pn -p 1-10000 $SERVER`
- [ ] Firewall active — `sudo ufw status verbose`
- [ ] SSH: keys only, no root password login — `sudo sshd -T | grep -E '^(passwordauthentication|permitrootlogin|kbdinteractiveauthentication)'` → `no`, `prohibit-password`, `no`
- [ ] fail2ban protecting SSH — `sudo fail2ban-client status sshd`
- [ ] Security updates automatic — `systemctl is-enabled unattended-upgrades`
- [ ] Only trusted people are in the `docker` group (it is root-equivalent) — `getent group docker`
- [ ] No container port on a public interface except nginx — `docker ps --format '{{.Names}}\t{{.Ports}}' | grep -v 127.0.0.1 | grep 0.0.0.0` lists only nginx (80, 443)

**Secrets**

- [ ] `.env` generated by `init-env.sh`, never committed, mode 600 — `stat -c '%a %U' /opt/copilot/app/.env && git -C /opt/copilot/app check-ignore .env`
- [ ] No placeholder secrets left — `grep -c 'change-me' /opt/copilot/app/.env` → `0`
- [ ] Backup passphrase exists, is not world-readable, and a copy is stored off the server — `stat -c '%a %U:%G' /etc/copilot/backup.pass` → `640 root:deploy`
- [ ] Backups are encrypted — `ls /opt/copilot/backups | grep -vc '\.gpg$\|^last-\|verify.log'` → `0`
- [ ] CI key can only deploy — `grep -c 'command="/opt/copilot/app/scripts/ci-entry.sh"' ~deploy/.ssh/authorized_keys`

**Transport and proxy**

- [ ] Trusted certificate, HTTP redirects to HTTPS, HSTS and the other headers present, TLS 1.0/1.1 refused — `./scripts/validate.sh` (section *Reverse proxy and TLS*)
- [ ] Session cookies are `Secure` and `HttpOnly` — `grep '^COOKIE_SECURE=' .env` → `true`; after a sign-in in the browser both cookies show Secure and HttpOnly
- [ ] `/metrics` not reachable publicly — `curl -s -o /dev/null -w '%{http_code}\n' https://$DOMAIN/metrics` → `404`
- [ ] Monitoring, Mailpit, MinIO console and the API port are loopback-only — `ss -ltn | grep -E ':(3001|9090|8025|9001|8000|3000) '` shows `127.0.0.1` only

**Application**

- [ ] Registration closed after the first organization — `curl -s -o /dev/null -w '%{http_code}\n' -X POST https://$DOMAIN/api/auth/register` → `403`
- [ ] Email verification required — `grep '^REQUIRE_EMAIL_VERIFICATION=' .env` → `true`
- [ ] Administrators and approvers have two-factor login enabled — in the app: *Team*
- [ ] The API connects as the restricted role and row-level security is forced on every tenant table —
  `docker compose exec db psql -U copilot -d copilot -Atc "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname='copilot_app'"` → `f|f`
  `docker compose exec db psql -U copilot -d copilot -Atc "SELECT count(*) FROM pg_class c JOIN information_schema.columns k ON k.table_name=c.relname AND k.column_name='organization_id' AND k.table_schema='public' WHERE c.relkind='r' AND NOT (c.relrowsecurity AND c.relforcerowsecurity)"` → `0`
- [ ] Virus scanning uses ClamAV — `grep '^SCAN_BACKEND=' .env` → `both`; `docker inspect --format '{{.State.Health.Status}}' $(docker compose ps -q clamav)` → `healthy`
- [ ] Four-eyes approvals on — `grep '^REQUIRE_SEPARATE_APPROVER=' .env` → `true`
- [ ] If an external AI provider is configured, that is a decision you made knowingly: document text is sent to it — `grep -E '^(ANTHROPIC_API_KEY|OPENAI_API_KEY)=.+' .env` (no output = nothing leaves the server)
- [ ] API documentation endpoints are off in production — `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/docs` → `404`

**Ongoing**

- [ ] Review the audit log and the assistant runs monthly (*Audit log*, *Assistant → All agent runs*)
- [ ] Rotate `JWT_SECRET`, database, MinIO and Grafana passwords yearly or when someone with access leaves (section 18, scenario E)
- [ ] Rebuild monthly to pick up base-image security fixes: `docker compose build --pull --no-cache backend frontend && ./scripts/deploy.sh`

---

## 17. Production readiness checklist

- [ ] DNS `A` record resolves to the server — `dig +short A $DOMAIN`
- [ ] Server sized per section 2; swap on — `free -h`
- [ ] `./scripts/validate.sh` prints `VALIDATION PASSED with 0 warning(s)`
- [ ] Trusted certificate with automatic renewal proven — `docker compose run --rm --entrypoint certbot certbot renew --webroot -w /var/www/certbot --dry-run`
- [ ] Real SMTP relay configured; a verification mail and a password-reset mail arrived in a real inbox — `grep '^SMTP_HOST=' .env` is not `mailpit`
- [ ] SPF/DKIM for the sender domain of `EMAIL_FROM` are set up at your mail provider (RFQs go to suppliers by email)
- [ ] First administrator created, two-factor login on, registration closed
- [ ] Roles assigned: at least one buyer and one approver who are different people (approvals are four-eyes)
- [ ] Backup: passphrase stored off the server, off-site copy configured, schedule installed — `cat /etc/cron.d/copilot`
- [ ] A full backup exists and its restore test passed — `tail -n1 /opt/copilot/backups/verify.log`
- [ ] A restore was rehearsed on a second machine (section 18, drill)
- [ ] Monitoring up, dashboard opens, no alert firing — `./scripts/check-monitoring.sh`
- [ ] Health probe scheduled and its notification channel tested — `./scripts/set-env.sh ALERT_WEBHOOK_URL …`, then `docker compose stop frontend; ./scripts/healthcheck.sh; docker compose start frontend`
- [ ] External uptime check on `https://$DOMAIN/healthz`
- [ ] Logs searchable in Grafana (dashboard *AI Sourcing Copilot - Logs* shows lines)
- [ ] `./scripts/loadtest.sh load` passes its thresholds on this server
- [ ] A deployment and a rollback were both performed once on this server — `./scripts/rollback.sh --list` shows both
- [ ] CI is green on `main`, and the deploy workflow has run successfully once (if used)
- [ ] Security checklist (section 16) complete
- [ ] Someone other than the person who set this up has followed section 10 successfully
- [ ] Decision recorded: which AI provider, if any, may receive document text (`AI_PROVIDER_ORDER`, `AGENT_MODE`)

---

## 18. Disaster recovery procedures

Targets with the default schedule: lose at most 1 hour of database changes and 24 hours of uploaded documents; be back within about 2 hours on a new server (most of it image build and virus-signature download).

What you need, kept **off** the server: the backup passphrase, SSH access to the off-site backup host, this repository (or its archive), and control of the DNS record.

### A. The server is gone

```bash
# 1. New server: sections 2 and 20, steps 1–3 (bootstrap, code in /opt/copilot/app). Do NOT run init-env.sh or deploy.sh.

# 2. As root: put the backup passphrase back
sudo sh -c 'umask 027; cat > /etc/copilot/backup.pass; chown root:deploy /etc/copilot/backup.pass'    # paste it, Enter, Ctrl-D

# 3. As deploy: give this server access to the off-site host, then fetch the newest full backup and hourly database dump
sudo -iu deploy
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 && cat ~/.ssh/id_ed25519.pub     # add this line to ~backup/.ssh/authorized_keys on the backup host
ssh-keyscan backup.example.com >> ~/.ssh/known_hosts
mkdir -p /opt/copilot/backups && cd /opt/copilot/backups
rsync -av "backup@backup.example.com:/srv/backups/copilot/$(ssh backup@backup.example.com 'ls -t /srv/backups/copilot | grep -v -- -db | head -n1')" .
rsync -av "backup@backup.example.com:/srv/backups/copilot/$(ssh backup@backup.example.com 'ls -t /srv/backups/copilot | grep -- -db-hourly | head -n1')" .

# 4. Restore: the full backup brings .env, the database and the documents; the hourly dump then brings the database forward
cd /opt/copilot/app
./scripts/restore.sh "$(ls -t /opt/copilot/backups/copilot-*Z.tar.gpg | head -n1)" --yes
./scripts/restore.sh "$(ls -t /opt/copilot/backups/copilot-*-db-hourly.tar.gpg | head -n1)" --yes

# 5. Point DNS at the new address, wait for it, then get the certificate and record the release
dig +short A $DOMAIN
./scripts/ssl-init.sh
./scripts/deploy.sh
./scripts/validate.sh

# 6. As root: schedule backups and health probes again
sudo /opt/copilot/app/scripts/install-cron.sh deploy
```

The restored `.env` carries the original secrets, so sessions, two-factor logins and stored documents keep working. Step 4 builds the images on first use (several minutes) and waits for ClamAV to download its signatures.

### B. Database damaged, or a bad change must be undone

```bash
cd /opt/copilot/app
ls -lt /opt/copilot/backups | head                     # pick the last archive from before the damage
./scripts/restore.sh /opt/copilot/backups/copilot-<time>-db-hourly.tar.gpg
```

### C. Documents lost (MinIO volume damaged), database intact

```bash
./scripts/restore.sh "$(ls -t /opt/copilot/backups/copilot-*Z.tar.gpg | head -n1)" --files-only
```

Documents uploaded after that backup are gone; their rows remain and show a download error. List them:

```bash
docker compose exec db psql -U copilot -d copilot -c "SELECT id, filename, created_at FROM documents WHERE created_at > '<backup time, UTC>' ORDER BY created_at"
```

### D. One record or one tenant must be recovered

Use the scratch-database procedure at the end of section 10 and copy the rows back by hand. Never restore a whole backup to recover one record.

### E. A secret is lost or compromised

```bash
cd /opt/copilot/app
# Session signing key: everyone is signed out
./scripts/set-env.sh JWT_SECRET "$(openssl rand -hex 48)" && docker compose up -d backend worker

# Application database password
NEW=$(openssl rand -hex 24)
docker compose exec db psql -U copilot -d postgres -c "ALTER ROLE copilot_app WITH PASSWORD '$NEW'"
./scripts/set-env.sh APP_DB_PASSWORD "$NEW" && docker compose up -d backend worker

# PostgreSQL superuser password
NEW=$(openssl rand -hex 24)
docker compose exec db psql -U copilot -d postgres -c "ALTER ROLE copilot WITH PASSWORD '$NEW'"
./scripts/set-env.sh POSTGRES_PASSWORD "$NEW" && docker compose up -d

# MinIO root password, metrics token
./scripts/set-env.sh MINIO_ROOT_PASSWORD "$(openssl rand -hex 24)" && docker compose up -d minio backend worker
./scripts/set-env.sh METRICS_TOKEN "$(openssl rand -hex 32)" && docker compose up -d backend prometheus

# Grafana admin password (the .env value only applies on first start)
NEW=$(openssl rand -hex 16); docker compose exec grafana grafana cli admin reset-admin-password "$NEW" && ./scripts/set-env.sh GRAFANA_ADMIN_PASSWORD "$NEW"

# CI deploy key: delete its line from ~deploy/.ssh/authorized_keys, then repeat section 8
./scripts/validate.sh && ./scripts/backup.sh       # the new .env must be in a backup
```

`MFA_ENCRYPTION_KEY` cannot be rotated in place. If it is **lost** (no backup of `.env`), users with two-factor login are locked out; reset them so they can sign in with their password and enroll again:

```bash
docker compose exec db psql -U copilot -d copilot -c "UPDATE users SET mfa_enabled = false, mfa_secret_enc = NULL, mfa_pending_secret_enc = NULL WHERE mfa_enabled" -c "DELETE FROM mfa_recovery_codes"
```

If the server itself may be compromised: rebuild on a new server (scenario A) from a backup taken before the compromise, then rotate every secret above, re-issue the certificate (section 7, force renewal), and have all users reset their passwords.

### F. Certificate expired or missing

```bash
docker compose logs --tail=50 certbot
./scripts/ssl-init.sh                                  # issues again if the certificate is missing
docker compose run --rm --entrypoint certbot certbot renew --webroot -w /var/www/certbot --force-renewal && docker compose exec nginx nginx -s reload
```

### G. Disk full

```bash
df -h / && docker system df
docker image prune -a -f --filter until=168h && docker builder prune -a -f
ls -lhS /opt/copilot/backups | head                    # delete old archives only if they exist off-site
sudo journalctl --vacuum-size=200M
docker compose up -d && ./scripts/validate.sh
```

### H. After a host reboot or Docker restart

Containers start by themselves. Confirm:

```bash
cd /opt/copilot/app && docker compose ps && ./scripts/validate.sh
```

### Drill (every quarter)

Rent a small server for an hour, follow scenario A with `ACME_STAGING=1 ./scripts/ssl-init.sh` and a hosts-file entry on your workstation instead of the DNS change (`echo "<drill server IP> $DOMAIN" | sudo tee -a /etc/hosts`), sign in, open a document, then delete the server and the hosts entry. Record how long it took.

---

## 19. Post-deployment validation

`scripts/validate.sh` is run automatically by `deploy.sh`, `rollback.sh` and `restore.sh`. Run it by hand any time:

```bash
cd /opt/copilot/app
./scripts/validate.sh
```

It checks, and prints PASS / FAIL / WARN for each:

| Group | Checks |
| --- | --- |
| Containers | every enabled service is running and healthy |
| Application | API liveness and readiness, front end, sign-in page served, anonymous API requests get 401, `/metrics` not reachable through the web, database schema at the latest revision |
| Metrics endpoint | 401 without the token, served with it, database gauge up, job queue moving |
| Reverse proxy and TLS | HTTP → HTTPS redirect, `/healthz`, security headers, no version disclosure, certificate trusted and not expiring within 14 days (WARN for the placeholder), TLS 1.0/1.1 refused |
| Host | only SSH/HTTP/HTTPS on public interfaces, disk below 85 %, last full backup within 26 hours, mail relay configured |
| Monitoring | Prometheus scrapes the API, Grafana healthy |
| Signed-in (optional) | sign-in, session, RFQ list, document list, an assistant answer with a cited source, sign-out |

Exit code 0 means no FAIL. Warnings do not fail a deployment but must be cleared before go-live (section 17).

Enable the signed-in checks with a dedicated read-only user (role auditor, no two-factor login, password without `"` or `\`):

```bash
./scripts/set-env.sh VALIDATE_EMAIL validate@example.com
./scripts/set-env.sh VALIDATE_PASSWORD 'the-password'
./scripts/validate.sh
```

From outside the server, after each release (your workstation):

```bash
curl -fsS https://$DOMAIN/healthz
curl -s -o /dev/null -w 'login %{http_code}\n' https://$DOMAIN/login
curl -s -o /dev/null -w 'anonymous api %{http_code}\n' https://$DOMAIN/api/auth/me            # 401
curl -sI https://$DOMAIN/login | grep -iE 'strict-transport|x-frame|x-content-type'
echo | openssl s_client -connect $DOMAIN:443 -servername $DOMAIN 2>/dev/null | openssl x509 -noout -issuer -enddate
```

Then, in the browser, the five-minute human check: sign in → upload a small document and watch it reach *processed* → open an RFQ → ask the assistant "What should we do next?" and open one cited source → *Audit log* shows your actions.

And a quick load sanity check: `LOADTEST_EMAIL=… LOADTEST_PASSWORD=… ./scripts/loadtest.sh smoke`.

---

## 20. Exact commands to deploy from a fresh Ubuntu server

Prerequisites: a new Ubuntu 24.04 server you can reach as `root` with your SSH key, ports 22/80/443 open at the provider, and the DNS `A` record for `$DOMAIN` pointing at it (section 2). The whole procedure takes 20–30 minutes, most of it unattended.

### Step 1 — put the code on the server

Variant A, from the archive (on your workstation):

```bash
export DOMAIN=copilot.example.com ACME_EMAIL=ops@example.com SERVER=203.0.113.10
scp ai-sourcing-copilot-ops.zip root@$SERVER:/root/
ssh root@$SERVER "apt-get update -qq && apt-get install -y -qq unzip && mkdir -p /opt/copilot \
  && unzip -q /root/ai-sourcing-copilot-ops.zip -d /opt/copilot && mv /opt/copilot/ai-sourcing-copilot /opt/copilot/app"
```

Variant B, from git (needed for the GitHub Actions pipeline in section 8):

```bash
export DOMAIN=copilot.example.com ACME_EMAIL=ops@example.com SERVER=203.0.113.10
ssh root@$SERVER "apt-get update -qq && apt-get install -y -qq git && mkdir -p /opt/copilot \
  && git clone https://github.com/YOUR_ORG/ai-sourcing-copilot.git /opt/copilot/app"
```

### Step 2 — prepare the server (as root)

```bash
ssh root@$SERVER
chmod +x /opt/copilot/app/scripts/*.sh /opt/copilot/app/infra/nginx/99-reload-certs.sh
/opt/copilot/app/scripts/server-bootstrap.sh
chown -R deploy:deploy /opt/copilot
sh -c 'umask 027; openssl rand -base64 48 > /etc/copilot/backup.pass; chown root:deploy /etc/copilot/backup.pass'
cat /etc/copilot/backup.pass       # save this passphrase in your password manager now
```

### Step 3 — configure and launch (still in the root session; runs as the deploy user)

```bash
export DOMAIN=copilot.example.com ACME_EMAIL=ops@example.com
sudo -iu deploy bash -lc "cd /opt/copilot/app \
  && ./scripts/init-env.sh \
  && ./scripts/configure-prod.sh $DOMAIN $ACME_EMAIL \
  && ./scripts/set-env.sh BACKUP_PASSPHRASE_FILE /etc/copilot/backup.pass \
  && ./scripts/deploy.sh"
```

This generates all secrets, builds the images, applies the database migrations, starts the application with nginx, Prometheus, Grafana, Loki and Alloy, obtains the Let's Encrypt certificate and validates the result. The first start waits for ClamAV to download its signatures (up to 10 minutes). It ends with `VALIDATION PASSED` and `Deployed r<timestamp>`; two warnings are expected at this point (no backup yet, mail still captured locally).

### Step 4 — schedule backups and health probes, take the first backup (root session)

```bash
/opt/copilot/app/scripts/install-cron.sh deploy
sudo -iu deploy bash -lc "cd /opt/copilot/app && ./scripts/backup.sh && ./scripts/verify-backup.sh"
```

### Step 5 — mail relay (root session; use your relay's values)

```bash
sudo -iu deploy bash -lc "cd /opt/copilot/app \
  && ./scripts/set-env.sh SMTP_HOST smtp.example.com \
  && ./scripts/set-env.sh SMTP_PORT 587 \
  && ./scripts/set-env.sh SMTP_STARTTLS true \
  && ./scripts/set-env.sh SMTP_USER copilot@example.com \
  && ./scripts/set-env.sh SMTP_PASSWORD 'the-relay-password' \
  && docker compose up -d"
```

No relay yet? Skip this step and read the verification mail in Mailpit: `ssh -N -L 8025:127.0.0.1:8025 deploy@$SERVER`, then http://localhost:8025.

### Step 6 — first administrator, then close registration

1. Browser: `https://copilot.example.com/register` → create the organization and administrator → follow the link in the verification mail → sign in → *Security* → enable two-factor login.
2. Root session:

```bash
sudo -iu deploy bash -lc "cd /opt/copilot/app && ./scripts/set-env.sh NGINX_REGISTER_DEFAULT 0 && docker compose up -d nginx && ./scripts/validate.sh"
```

### Step 7 — confirm from your workstation

```bash
curl -fsS https://$DOMAIN/healthz                                               # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://$DOMAIN/api/auth/register   # 403
ssh -N -L 3001:127.0.0.1:3001 deploy@$SERVER &                                  # Grafana at http://localhost:3001 (admin / GRAFANA_ADMIN_PASSWORD in .env)
```

The system is live. Before real use, work through sections 9 (off-site copy), 16 and 17.

### The same thing on a machine that is not a server (evaluation)

```bash
unzip -q ai-sourcing-copilot-ops.zip && cd ai-sourcing-copilot
make up                      # http://localhost:3000, mail at http://localhost:8025
make smoke
```
