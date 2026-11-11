# Running AI Sourcing Copilot on an Android phone (Termux)

This runs the whole platform on your phone: the database, the API, the background worker and the website. Only
your phone can open it (at `http://localhost:3000`). It is good for trying everything out and for running the
back office yourself; for real customers, use a server (see `docs/DEPLOYMENT.md`).

Everything you type is in a grey box. Type it exactly, then press **Enter**. Wait for each step to finish (you
see the `$` prompt again) before the next one.

## What you need

- An Android phone with Android 7 or newer, at least **4 GB of free storage** and ideally **4 GB of RAM or more**.
- Wi-Fi, and the phone on the charger for the first setup (it takes **30-60 minutes**: parts of the app are
  compiled on the phone).
- Your GitHub login (the code is in a private repository).

---

## Part 1: Install Termux (once)

1. Install **Termux** from **F-Droid** (https://f-droid.org/packages/com.termux/) or from Termux's GitHub page
   (https://github.com/termux/termux-app/releases). **Do not use the Google Play version**: it is outdated and
   the setup will fail.
2. Open Termux. You will see a black screen with a `$` prompt.
3. Stop Android from pausing Termux: **Settings > Apps > Termux > Battery > Unrestricted** (the wording varies by
   phone; look for "battery optimisation" and turn it off for Termux).

## Part 2: Download the code (once)

4. Update Termux and install the GitHub tools:

   ```
   pkg update -y && pkg install -y git gh
   ```

   If it asks a question about a configuration file, just press **Enter**.

5. Sign in to GitHub:

   ```
   gh auth login
   ```

   Answer with the arrow keys and Enter: **GitHub.com** > **HTTPS** > **Yes** (authenticate Git) > **Login with a
   web browser**. It shows an 8-character code: copy it, press Enter, and approve it in the browser that opens.

6. Download the code:

   ```
   gh repo clone pullomdarius7-bot/AI-Sourcing-Copilot ~/AI-Sourcing-Copilot -- -b ccr-b945e3ef-n46e9q
   ```

   (Once this work is merged into `main`, leave out `-- -b ccr-b945e3ef-n46e9q`.)

7. Go into the folder (do this every time you open Termux):

   ```
   cd ~/AI-Sourcing-Copilot
   ```

## Part 3: Set everything up (once, 30-60 minutes)

8. Run the setup:

   ```
   bash scripts/termux/setup.sh
   ```

   It works through 7 numbered steps and prints `==> Step N/7` as it goes. It installs the packages, creates your
   private settings file with generated passwords (`~/.copilot/env`), creates the database, installs the Python
   packages (the slowest part), sets up the database tables and builds the website. Keep Termux open and the
   screen on.

   When it finishes, you see **Setup finished.** If it stops with a red `ERROR`, see *Troubleshooting* below; you
   can always run the same command again: steps that are done are skipped.

## Part 4: Start the app and make yourself the admin

9. Start everything:

   ```
   bash scripts/termux/start.sh
   ```

   Wait for **Running. Open http://localhost:3000 in your phone's browser.**

10. Open Chrome (or any browser) on the phone and go to **http://localhost:3000**. Click **Start free trial**,
    register with your email and a password, and complete the short onboarding. (No verification email is needed
    on the phone.)

11. Back in Termux, give your account the back office (use the email you just registered with):

    ```
    bash scripts/termux/ops.sh grant-platform-admin you@example.com
    ```

12. In the browser, refresh the page. **Back office** now appears at the bottom of the menu. Only accounts you
    grant this way ever see it; for everyone else the page does not exist.

---

## Everyday use

Always start with `cd ~/AI-Sourcing-Copilot`.

| What | Command |
|---|---|
| Start | `bash scripts/termux/start.sh` |
| Stop | `bash scripts/termux/stop.sh` |
| Is it running? Health checks | `bash scripts/termux/status.sh` |
| Back up the database and files | `bash scripts/termux/backup.sh` |
| Get the latest version (backs up first) | `bash scripts/termux/update.sh` |
| List / add / remove admins | `bash scripts/termux/ops.sh list-platform-admins` / `grant-platform-admin EMAIL` / `revoke-platform-admin EMAIL` |

The app keeps running while Termux is open in the background (a Termux notification shows it). Restarting the
phone stops it: open Termux and run `start.sh` again. Your data is kept.

## The back office

In the website, **Back office** has six tabs:

- **Health**: platform totals (accounts, users, sign-ups, trials, paying accounts, credits used, failed jobs) and
  the system checks: database, file storage, background worker, AI, web search, payments, email, supplier
  invitations, virus scanning. Each shows **OK**, **WARN** (works, but a feature is off) or **FAIL** (fix it), in
  plain words. The same checks run in Termux with `bash scripts/termux/status.sh`.
- **Accounts**: search any account by business name, a user's email or Stripe customer id. Open one to see its plan,
  credits, users (with two-factor status and last sign-in) and credit history, and to **add or remove credits**,
  **extend a trial by 7 days**, or **deactivate / reactivate a user** (deactivating signs them out everywhere).
- **Invitations**: every supplier the AI sourcing engine invited, with the real business name, email and website
  (buyers never see these). **Send now** resends one immediately (it skips the waiting period and daily limit, but
  never emails a business that unsubscribed); **Hold** stops it.
- **Opt-outs**: addresses and domains that unsubscribed. **Lift** only when that business asks you, in writing.
- **Jobs**: background work for every account. Failed jobs show the error and a **Retry** button.
- **Audit**: everything done in the back office: who, when, what and the reason. Every fix asks you for a
  reason, and the log cannot be edited or read by customers.

## Turning on optional features

Your settings are in `~/.copilot/env`. To edit them:

```
pkg install -y nano
nano ~/.copilot/env
```

Fill in the lines you need (for example `ANTHROPIC_API_KEY=...`), save with **Ctrl+O**, **Enter**, exit with
**Ctrl+X** (on the Termux keyboard row, CTRL is a key above the keyboard). Then restart:

```
bash scripts/termux/stop.sh && bash scripts/termux/start.sh
```

| Feature | Lines to fill in |
|---|---|
| AI analysis | `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY` |
| AI web sourcing | `SEARCH_PROVIDER=brave` and `BRAVE_API_KEY` |
| Supplier invitations | `INVITE_SENDER_POSTAL_ADDRESS` (your business postal address; required by anti-spam law) |
| Payments | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` and the `STRIPE_PRICE_...` ids (Stripe cannot reach a phone, so subscriptions only complete on a real server) |

Do not change the passwords or keys at the top of the file: the database and existing sign-ins depend on them.

Emails (password resets, invitations) are not sent from the phone; they are written to the log. To read them:

```
grep -A 30 "EMAIL to=" ~/.copilot/logs/api.log ~/.copilot/logs/worker.log | tail -60
```

## Where things are

| | |
|---|---|
| Code | `~/AI-Sourcing-Copilot` |
| Settings and generated passwords (private) | `~/.copilot/env` |
| Database | `~/.copilot/pgdata` |
| Uploaded files | `~/.copilot/uploads` |
| Logs | `~/.copilot/logs` (`api.log`, `worker.log`, `web.log`, `postgres.log`) |
| Backups | `~/.copilot/backups` |

To copy backups to the phone's Downloads folder: run `termux-setup-storage` once (allow the permission), then
`cp ~/.copilot/backups/* ~/storage/downloads/`.

## Troubleshooting

| What you see | What to do |
|---|---|
| `ERROR: Run this inside the Termux app on Android` | Use the Termux app, not another terminal app. |
| Setup stops while installing Python packages (`error: failed to build ...`) | Run `pkg upgrade -y`, then `bash scripts/termux/setup.sh` again. Make sure at least 4 GB is free. |
| Setup stops while building the website (`JavaScript heap out of memory` or the app closes) | Close other apps and run `NODE_MEMORY_MB=1536 bash scripts/termux/setup.sh`. |
| `PostgreSQL did not start` | Run `cat ~/.copilot/logs/postgres.log`. If it says another server is running, run `bash scripts/termux/stop.sh` and start again. |
| `The app did not answer within 2 minutes` | Run `bash scripts/termux/status.sh`; anything marked STOPPED has a log file named next to it: read its end with `tail -50 ~/.copilot/logs/api.log` (or `worker.log`, `web.log`). |
| The browser says "This site can't be reached" | The app is not running: `bash scripts/termux/start.sh`. Use `http://` (not https) and `localhost:3000`. |
| Back office missing from the menu | Run `bash scripts/termux/ops.sh list-platform-admins`, check your email is listed, then sign out and in again. |
| It stops working after a while in the background | Turn off battery optimisation for Termux (Part 1, step 3). |

## What was and was not tested

The scripts were run end to end in a Linux stand-in for Termux, as an ordinary user: setup, start, stop, status,
backup, registering in a browser, granting the admin with `ops.sh` and using the back office. The website build was
also checked with the WebAssembly compiler that Next.js uses on Android. They have **not** been run on a real
Android phone: package compilation in step 4 and the website build depend on the phone, so if a step fails there,
send the last 30 lines it printed.
