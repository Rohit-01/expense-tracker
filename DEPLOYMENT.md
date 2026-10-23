# Pocket on your VPS: PM2 + Nginx

Prepared for `pocket.mrox.in`, the existing `kayo` PM2 account, and a checkout under `/home/kayo/apps`. Commands below assume `/home/kayo/apps/pocket`; use the directory that actually contains `production.py`. These are instructions only: no GitHub push or VPS change has been performed.

## Upgrade the existing app

Run PM2 as `kayo`, the same user who manages the existing apps. Preserve Pocket's current environment/storage values. This guide assumes the earlier example: `/home/kayo/.local/share/pocket/{data,auth.json,vault-export}`. Check the existing Pocket ecosystem file before proceeding; if you configured different paths, substitute them throughout. Do not point the release at an empty replacement data directory.

1. Back up the current installation **before pulling**. Stop only Pocket to make the legacy file copy consistent; there will be brief downtime. Execute each step separately and stop if any command fails.

```bash
cd /home/kayo/apps/pocket
pm2 stop pocket
umask 077
pocket_storage="$HOME/.local/share/pocket"
pocket_backup="$pocket_storage/backups/before-accounts-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$pocket_backup"
cp -a "$pocket_storage/data" "$pocket_backup/data"
install -m 600 "$pocket_storage/auth.json" "$pocket_backup/auth.json"
git rev-parse HEAD > "$pocket_backup/code-revision.txt"
```

This copies all current databases, including accounts if this is a later upgrade. Keep backups and credentials outside the Git checkout. The exported Markdown is regenerated from the databases; it is not the authoritative backup.

2. Pull the release and install its dependencies into the existing virtual environment.

```bash
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -p 'test_*.py' -v
```

Tests use temporary synthetic accounts/records, not your production data. There is no JavaScript build step.

3. Keep the current `POCKET_DOMAIN`, `POCKET_DATA_DIR`, `POCKET_AUTH_FILE`, and `POCKET_VAULT`. Restart Pocket:

```bash
pm2 restart pocket
pm2 status pocket
pm2 logs pocket --lines 50 --nostream
curl -fsS -H 'Host: pocket.mrox.in' http://127.0.0.1:8768/healthz
```

The first successful startup automatically imports the existing username and scrypt password hash from `auth.json`, assigns it ownership of your existing `ledger.sqlite3`, and creates `accounts.sqlite3`. This is transactional and repeatable. Missing credentials or an account database without an owner stop startup; public signup cannot claim your old records. No separate migration command is needed.

After migration, `auth.json` remains a protected rollback artifact and no longer controls login. Do not rerun `create_login.py` to change a migrated password. Sessions and owner sync tokens persist through PM2 restarts. This update also creates the sync-token table automatically; it preserves the existing accounts and ledgers.

If you edited the ecosystem file, restart from that file instead of using just the process name:

```bash
pm2 restart "$HOME/.config/pocket/ecosystem.config.js" --only pocket
```

Use your actual ecosystem path if different. `deploy/ecosystem.config.js` is an example for comparison; do not copy it over an existing config without preserving your storage paths. Keep one Gunicorn worker and one PM2 instance. The per-ledger Markdown writer uses process-local locks.

4. Check Pocket's existing Nginx site. The app itself no longer sends `WWW-Authenticate`. If Nginx also has `auth_basic`, remove it from Pocket's location, or set `auth_basic off;` there to override inherited settings. `deploy/nginx-pocket-location.conf` is a replacement for Pocket's existing `location /` block. Keep the current HTTPS certificate directives and HTTP-to-HTTPS redirect. Keep other apps' server blocks unchanged.

Only if Nginx configuration changed:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

For HTTPS Obsidian sync, replace `proxy_set_header Authorization "";` with `proxy_set_header Authorization $http_authorization;` in Pocket’s location. Otherwise Nginx strips the sync token and the endpoint returns 401. Never add authorization headers to access-log formats. The proxy must overwrite forwarded headers, as the example does. Gunicorn stays bound to `127.0.0.1:8768`. Do not expose that port externally.

5. Verify through the public site:

```bash
curl -fsS https://pocket.mrox.in/healthz
curl -sS -o /dev/null -w '%{http_code}\n' https://pocket.mrox.in/login
curl -sS -o /dev/null -w '%{http_code}\n' https://pocket.mrox.in/api/state
```

Expect `{"status":"ok"}`, `200`, and `401`, respectively. Open a private browser window: `/` should redirect to a branded login page with no browser popup. Your original username/password should show your existing expenses, loans, repayments, and reminders. Check the owner ledger before inviting registrations. Sign up a separate test account and verify it starts empty; log out and confirm protected pages are inaccessible.

If the popup persists, inspect the Pocket-specific Nginx `auth_basic` settings. If the site shows 502, inspect Pocket's PM2 logs for startup/configuration errors. A successful `/healthz` checks the running process; also check login and records to validate migration.

After successful verification:

```bash
pm2 save
```

The release also includes a web app manifest and Pocket home-screen icons. After the new version is live, open `/login` on a phone, add Pocket to the Home Screen, and verify that its icon opens the HTTPS domain and that login still works. The manifest and icons must remain publicly readable; financial APIs still require login. No separate phone package, build step, or native app store upload is involved.

Use your existing PM2 boot service. Never run `pm2 restart all` for a Pocket update. Later ordinary code-only updates need `git pull --ff-only` and `pm2 restart pocket`; stop Pocket before changing dependencies. Future database-changing releases may need their own migration steps.

## Accounts and Obsidian privacy

| Location, relative to POCKET_DATA_DIR | Purpose |
| --- | --- |
| `accounts.sqlite3` | Usernames, scrypt hashes, hashed session and owner sync credentials, CSRF tokens, rate-limit counters |
| `ledger.sqlite3` | Your existing owner records |
| `accounts/<UUID>/ledger.sqlite3` | One other account's private records |
| `accounts/<UUID>/export/` | That account's separate generated Markdown |

Only the owner ledger writes to `POCKET_VAULT/ai zone/openai agent/Personal/Finance/Expense Ledger.md`. Other users never write there. Username, password hash, sessions, and other account details are not written to the financial notes. User IDs are generated by the server and selected from the authenticated session, not URL parameters or form inputs.

The VPS export and your Mac's Obsidian vault are separate until a sync mechanism is configured. Sync **only the owner `POCKET_VAULT` export**, never the entire data directory or another user's export. `/api/ledger.md`, `/api/export.json`, and `/api/reminder.ics` return only the signed-in user's records. Those downloads require a browser session. The separate `/api/obsidian/ledger.md` endpoint accepts only the owner’s read-only bearer token. The owner can generate, replace, and revoke it using **Obsidian sync** in the dashboard. See [OBSIDIAN_SYNC.md](OBSIDIAN_SYNC.md) for the prepared HTTPS/Keychain helper and remaining Mac installation steps. Deploying the API alone does not start synchronization on your Mac.

Anyone can register with a unique username and a password of at least 14 characters. Sessions expire after seven days. Persistent limits allow ten failed logins per IP per five minutes and five successful signups per IP per hour; users sharing a public IP share those limits. No email address is collected and there is no email recovery.

## Administrator password reset

Run this interactively as the data-owning VPS user. Enter the username and new password at the prompts, not in a shell command:

```bash
cd /home/kayo/apps/pocket
.venv/bin/python deploy/reset_password.py --data-dir "$HOME/.local/share/pocket/data"
```

It asks for confirmation, updates that account only, and revokes all its sessions and sync tokens immediately. No restart is required. Verify the requester's identity before using it for another person.

## Back up and restore

Complete backup of the registry and all registered users' ledgers:

```bash
.venv/bin/python deploy/backup.py \
  --data-dir "$HOME/.local/share/pocket/data" \
  --directory "$HOME/.local/share/pocket/backups"
```

The command prints a snapshot directory. A `manifest.json` appears only after every database passes an integrity check. Keep snapshots private: they contain all users' financial data and password hashes. Copy them securely off the VPS. Generated Markdown is omitted and regenerated when the app opens each ledger.

The SQLite online backup API permits backups while running. It captures the account registry first, then each ledger at a consistent point for that database; it is not a simultaneous snapshot across all users. Accounts registered afterward appear in the next backup. For a precisely timed whole-installation snapshot, stop only Pocket during the backup and restart afterward. Replace old single-file `--source` backup schedules with the complete `--data-dir` invocation; `--source` remains for legacy single-ledger use only.

Restore into a **new, nonexistent** directory for verification first:

```bash
.venv/bin/python deploy/restore.py \
  --snapshot "$HOME/.local/share/pocket/backups/pocket-REPLACE_WITH_SNAPSHOT_TIMESTAMP" \
  --data-dir "$HOME/.local/share/pocket/data-restored"
```

Restore verifies ownership mappings and SQLite integrity, and revokes all saved sessions and sync tokens. Generate a new sync token and rerun Mac setup after a restore. It refuses to overwrite any existing destination. To use the restored data, stop Pocket, change `POCKET_DATA_DIR` in the ecosystem file to the restored path, preserve `POCKET_VAULT`, then restart from that ecosystem file. Keep the prior data directory until verification is complete. A legacy snapshot without `accounts.sqlite3` also needs the matching old `auth.json` to establish the owner.

Do not assume reverting Git restores accounts or money records. Before undoing a release that accepted new writes, take a complete backup; restoring an older snapshot would omit those later writes. The pre-upgrade code revision, databases, and protected legacy credentials form the rollback set for the initial migration.

## New installations only

For an existing deployment, skip this section. Clone your GitHub repository into the chosen checkout, create `.venv` using `python3 -m venv .venv`, and install `requirements.txt`. Choose a private storage directory outside the checkout. Bootstrap the owner once with `deploy/create_login.py --output /your/private/storage/auth.json`. Set all four `POCKET_*` values in the ecosystem file, then use `pm2 start /path/to/ecosystem.config.js --only pocket`. Route the domain through its own HTTPS Nginx server block and verify as above.

The original `deploy/pocket.service` and Caddy sample remain alternatives for separate installations. This deployment uses PM2 and Nginx; do not enable a second service on port 8768. The local Mac `Launch Pocket.command` continues to run a single-user loopback app without public accounts.

## References

- [PM2 ecosystem configuration and selecting one process](https://pm2.keymetrics.io/docs/usage/application-declaration/)
- [Nginx Basic authentication and overriding inherited authentication](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html)
- [Flask security guidance: cookies, CSRF, hosts, and headers](https://flask.palletsprojects.com/en/stable/web-security/)
