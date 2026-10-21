# Deploy Pocket from GitHub to your VPS

Prepared for **Ubuntu 24.04**, a dedicated Linux account with sudo access, SSH on the standard port, and a domain you control. Replace `YOUR_GITHUB_USER`, `YOUR_VPS_USER`, `YOUR_VPS_IP`, and `pocket.yourdomain.com` throughout. Adapt firewall rules first if SSH uses a different port. These are instructions; no GitHub repository or VPS deployment has been created by this guide.

The production path is **Caddy → Gunicorn → Pocket → SQLite**. Browser access is protected by a username and password over HTTPS. The normal `server.py` and Mac launcher remain available for local use. Do not expose the local server directly to the internet.

## 1. Push only the application code from your Mac

Create a **private, empty** GitHub repository called `pocket`. Do not initialize it with a README, license, or .gitignore; those files already exist locally where applicable.

Authenticate Git with your usual GitHub credentials. If you use GitHub CLI, run `gh auth login` and `gh auth setup-git`. Do not paste credentials into code or repository URLs. GitHub account passwords do not authenticate HTTPS Git pushes.

In your Mac Terminal:

```bash
cd '/Users/Rohit/Documents/Codex/2026-10-20/ma/outputs/expense-tracker'
git init -b main
git add .
git status --short
git diff --cached --stat
```

The supplied `.gitignore` excludes the database, backups, your local `config.json`, login files, environments, and generated exports. **Check the staged list before committing.** If you previously committed private files, adding .gitignore alone does not remove them from Git history.

Then:

```bash
git commit -m "Prepare Pocket for VPS deployment"
git remote add origin https://github.com/YOUR_GITHUB_USER/pocket.git
git push -u origin main
```

Use these initialization commands once only. Future code changes use `git add`, `git commit`, and `git push`.

## 2. Point a subdomain to the VPS

At your DNS provider, add an A record:

| Type | Name | Value |
| --- | --- | --- |
| A | pocket | YOUR_VPS_IP |

Use the actual subdomain in both the app environment and Caddy configuration below. Add an AAAA record only if your VPS has correctly configured public IPv6. Wait until DNS resolves to this VPS before requesting the HTTPS site.

## 3. Prepare the VPS and GitHub access

Connect from your Mac:

```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP
```

On the VPS:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip curl ca-certificates gnupg ufw
python3 -c "import sqlite3; print(sqlite3.sqlite_version)"
```

The last command verifies Python's SQLite support. No separate database service is required.

If this VPS does not already have read access to your private repository, create a dedicated SSH deploy key:

```bash
ssh-keygen -t ed25519 -f "$HOME/.ssh/pocket_github" -C 'pocket-vps-readonly'
cat "$HOME/.ssh/pocket_github.pub"
```

Add **only the public key** to GitHub → repository → Settings → Deploy keys → Add deploy key. Leave **Allow write access unchecked**. Keep the private key on the VPS. If you set a key passphrase, load it into an SSH agent when pulling; do not put it in scripts. Verify GitHub's SSH host fingerprint against its official documentation when first connecting.

Clone the code as your normal deployment user, not as the Pocket service account:

```bash
sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 755 /opt/pocket
git -c core.sshCommand="ssh -i $HOME/.ssh/pocket_github -o IdentitiesOnly=yes" clone git@github.com:YOUR_GITHUB_USER/pocket.git /opt/pocket
cd /opt/pocket
git config core.sshCommand "ssh -i $HOME/.ssh/pocket_github -o IdentitiesOnly=yes"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

If GitHub SSH access already uses another configured key, omit the custom `core.sshCommand` options and use your existing setup.

## 4. Create the service account, persistent storage, and login

On the VPS:

```bash
sudo useradd --system --user-group --home-dir /var/lib/pocket --shell /usr/sbin/nologin pocket
sudo install -d -o pocket -g pocket -m 700 /var/lib/pocket
sudo -u pocket /opt/pocket/.venv/bin/python /opt/pocket/deploy/create_login.py
```

Enter your chosen Pocket username and a unique password at the prompts. The password is hidden while typing and only its scrypt hash is saved. It is not a GitHub password. The credential file is outside the code checkout at `/var/lib/pocket/auth.json`.

Install the environment file:

```bash
sudo install -o root -g pocket -m 640 deploy/pocket.env.example /etc/pocket.env
sudo nano /etc/pocket.env
```

Set `POCKET_DOMAIN` to the real domain, without `https://` or a trailing slash. Keep these storage paths unless you deliberately adjust the service permissions too:

```ini
POCKET_DOMAIN=pocket.yourdomain.com
POCKET_DATA_DIR=/var/lib/pocket/data
POCKET_AUTH_FILE=/var/lib/pocket/auth.json
POCKET_VAULT=/var/lib/pocket/vault-export
PYTHONDONTWRITEBYTECODE=1
```

Data stays in `/var/lib/pocket/`, separate from `/opt/pocket/`. A Git pull will not replace it.

## 5. Transfer existing records, if you have any

Skip this section for a fresh, empty ledger. The Git repository deliberately contains no financial records.

Before migrating, pause entering new data into the local app. On your Mac, create a consistent database snapshot:

```bash
cd '/Users/Rohit/Documents/Codex/2026-10-20/ma/outputs/expense-tracker'
python3 deploy/backup.py --source data/ledger.sqlite3 --directory backups
```

The command prints the snapshot filename. Substitute that path below:

```bash
scp 'backups/ACTUAL_SNAPSHOT_FILENAME.sqlite3' YOUR_VPS_USER@YOUR_VPS_IP:pocket-migration.sqlite3
```

On the VPS, **before the first service start**:

```bash
sudo install -d -o pocket -g pocket -m 700 /var/lib/pocket/data
sudo test ! -e /var/lib/pocket/data/ledger.sqlite3 && sudo install -o pocket -g pocket -m 600 "$HOME/pocket-migration.sqlite3" /var/lib/pocket/data/ledger.sqlite3
```

If the destination already exists, this command will not replace it. Stop and make a deliberate restore/merge plan instead. After migration, use the VPS app for all new records. Do not continue entering separate records in the old local tracker. Remove staging copies only after you verify the VPS records and have a backup.

## 6. Run Pocket as a persistent service

On the VPS:

```bash
cd /opt/pocket
sudo install -m 644 deploy/pocket.service /etc/systemd/system/pocket.service
sudo systemctl daemon-reload
sudo systemctl enable --now pocket
sudo systemctl status pocket --no-pager
curl -fsS -H 'Host: pocket.yourdomain.com' http://127.0.0.1:8768/healthz
```

Replace the Host header with your domain. Expected response: `{"status":"ok"}`. The health endpoint contains no records; data endpoints require HTTPS and login.

This service starts on boot and restarts on failure. It uses **one Gunicorn worker with four threads**. Keep one worker: the current ledger-export lock and request token live within that worker. Restart the service for deployments; do not use overlapping worker reloads.

## 7. Install Caddy and enable HTTPS

The following installs Caddy from its official stable Debian/Ubuntu repository. If Caddy is already installed, skip installation and add the new site without replacing other sites.

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

If another web server already owns ports 80/443, integrate Pocket with that server or plan a switch first; do not stop existing sites blindly.

Edit the Caddyfile:

```bash
sudo nano /etc/caddy/Caddyfile
```

Add the following site block (also provided in `deploy/Caddyfile`), with your real domain:

```caddyfile
pocket.yourdomain.com {
    reverse_proxy 127.0.0.1:8768
}
```

Pocket handles the login itself. Caddy forwards HTTPS requests to its private local port. The app trusts forwarded protocol/client information only from this loopback proxy arrangement; do not bind Gunicorn to `0.0.0.0`.

For a VPS using standard SSH port 22, configure the host firewall:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw status
sudo ufw enable
```

If SSH uses another port, allow that actual port **before** enabling UFW. Preserve any firewall rules needed by existing services. In your VPS provider's firewall, also permit web traffic on 80/443 and your SSH access. Do not open 8768 publicly.

Validate and start/reload Caddy:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

With correct DNS and reachable ports, Caddy obtains and renews the HTTPS certificate automatically.

## 8. Open and verify the production app

Open **https://pocket.yourdomain.com** in your browser. Enter the username/password created in step 4. This is a browser login prompt, not a custom account-registration screen. On a shared computer, use a private browser window and close it when finished; browsers can retain HTTP Basic credentials.

Verify unauthenticated access is blocked:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://pocket.yourdomain.com/api/state
curl -s -o /dev/null -w '%{http_code}\n' https://pocket.yourdomain.com/api/export.json
```

Both should return **401**. Enter a real expense, refresh, and verify it is still present. If you migrated records, check the expense total and outstanding-loan balances before entering more data. Restart Pocket and confirm persistence:

```bash
sudo systemctl restart pocket
sudo systemctl is-active pocket caddy
sudo systemctl is-enabled pocket caddy
```

This confirms service status and boot configuration. To verify recovery after an actual VPS reboot, schedule a reboot when it will not interrupt other workloads, then test the site again. A single VPS can still have downtime during maintenance or provider outages.

If startup fails, inspect these logs; do not paste secrets or personal record contents into public issues:

```bash
sudo journalctl -u pocket -n 60 --no-pager
sudo journalctl -u caddy -n 60 --no-pager
```

## 9. Enable backups

```bash
cd /opt/pocket
sudo install -m 644 deploy/pocket-backup.service /etc/systemd/system/pocket-backup.service
sudo install -m 644 deploy/pocket-backup.timer /etc/systemd/system/pocket-backup.timer
sudo systemctl daemon-reload
sudo systemctl start pocket-backup.service
sudo systemctl enable --now pocket-backup.timer
systemctl list-timers pocket-backup.timer --no-pager
```

The timer makes a consistent daily SQLite snapshot, with an integrity check, under `/var/lib/pocket/backups/`. It runs around 02:15–02:25 UTC (07:45–07:55 India time) and catches up after downtime. Backups are not automatically deleted. Plan retention and copy backups securely off the VPS; same-server backups do not protect against loss of the VPS. Test a restore into a separate temporary database before relying on your backup process.

## 10. Bring the ledger into Obsidian

The VPS exports a note under:

```text
/var/lib/pocket/vault-export/ai zone/openai agent/Personal/Finance/Expense Ledger.md
```

The interface says **Ledger exported on VPS**. That means a server-side file was written; it does **not** mean your Mac has received it.

You can get a fresh, authenticated Markdown export at:

```text
https://pocket.yourdomain.com/api/ledger.md
```

For an immediate manual update, download it after signing in and place it at `ai zone/openai agent/Personal/Finance/Expense Ledger.md` in your Mac's vault. Preserve any manual changes before replacing the generated note. Do not run the old local tracker as a second writer to that same note after switching to the VPS.

Automatic transfer to your Mac is a separate setup: configure a narrowly scoped SSH pull or authenticated download on the Mac, with credentials held securely outside Git. Your Mac then refreshes the note while online. The production files in this guide do not claim or configure that recurring Mac sync.

Calendar export continues to work. Hosting does not add push notifications; imported calendar events still require a calendar app and do not automatically track later edits in Pocket.

## Future code updates

On your Mac, commit and push your changes. On the VPS, first make a backup, then pull and restart:

```bash
sudo systemctl start pocket-backup.service
cd /opt/pocket
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart pocket
sudo systemctl status pocket --no-pager
```

For dependency changes, stop Pocket before changing its live virtual environment, then start it again. If a deployment changes database schema, follow that release's migration/rollback steps instead of assuming a Git rollback will restore data. A short restart causes brief downtime. Refresh already-open pages afterward to obtain the new request token.

If service templates change, install the changed unit files and run `sudo systemctl daemon-reload` before restarting. If Caddy config changes, validate and reload Caddy. Your database, login, environment, and exports remain outside the repository.

## References

- GitHub: [Add local code to GitHub](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github), [deploy keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys), [SSH fingerprints](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints).
- Flask: [Gunicorn deployment](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/).
- Caddy: [installation](https://caddyserver.com/docs/install), [automatic HTTPS](https://caddyserver.com/docs/automatic-https).
- systemd: [service configuration](https://www.freedesktop.org/software/systemd/man/257/systemd.service.html).
