# Pocket → Obsidian over HTTPS

Local implementation prepared on 2026-10-22. Nothing has been pushed, deployed to the VPS, installed in Keychain, or scheduled on this Mac. Your actual vault has not been changed by these implementation tests.

## Steps 1–4: implemented locally

1. **Owner token controls:** the production dashboard shows **Obsidian sync** only to the owner. Generate a token, copy it once, replace it, or revoke it. Tokens expire after 90 days. Only a SHA-256 hash, owner UUID, fixed `obsidian:read` scope, and timestamps are stored in `accounts.sqlite3`. There is one token per owner; replacement immediately invalidates the previous one. Browser login and CSRF checks protect changes.
2. **Read-only API:** `GET https://pocket.mrox.in/api/obsidian/ledger.md` accepts `Authorization: Bearer <token>`. It returns only the owner’s generated Markdown and a server-assigned owner identifier. Browser cookies alone do not grant access. Other users, URL account selectors, expired/revoked tokens, and write methods cannot use it. This credential cannot authenticate to any other API. Logout ends the browser session; it deliberately leaves the independent sync connection active. Password reset and backup restore revoke both sessions and sync tokens.
3. **Mac helper:** `deploy/sync_obsidian.py` uses the native macOS Keychain backend, verified HTTPS, a fixed endpoint, no redirects, a size limit, and owner/Markdown validation. The VPS already renders Markdown, so the helper downloads that exact generated view. No SSH password or token environment variable is needed. Setup takes hidden interactive input and never puts a token in process arguments, config, notes, or logs. It verifies the token and Keychain storage but does not change your ledger or create a schedule.
4. **Verification:** tests cover owner isolation, CSRF, token scope/expiry/revocation/rotation, persistence across app restarts, backup restore, API-to-temporary-vault sync, Keychain failures, offline/error responses, redirect rejection, incomplete downloads, safe logs, manual content protection, and atomic writes. Browser checks use only disposable local accounts. Real VPS connectivity and real Keychain authorization remain installation checks.

Verified locally: **44 account/sync tests and 8 core ledger tests passed**. JavaScript syntax and Git whitespace checks passed. Desktop and 390-pixel mobile browser checks covered generation, replacement, revocation, token clearing, keyboard focus/Escape, and logout, with no browser console errors. The native Keychain backend loaded successfully in the isolated test environment; actual credential storage was tested with an adapter, not your real Keychain.

The destination inside your selected vault is:

```text
ai zone/openai agent/Personal/Finance/Expense Ledger.md
```

This is a one-way copy of the owner’s financial records. Other users’ records and the authentication database never enter your vault. Your SQLite records on the VPS remain authoritative. Edit finances in Pocket and keep manual notes in separate notes. The helper will stop if it detects edits inside a previously synced note; move those edits to a manual note before restoring the generated version. Generated-file markers are reserved for the helper.

## Step 5: publish the reviewed code yourself

Review the local changes and push the code to your GitHub repository when ready. Keep real databases, exports, `config.json`, credentials, and local environments out of Git. The ignore rules cover these. Do not commit sync tokens anywhere.

## Step 6: deploy on the VPS

Follow [DEPLOYMENT.md](DEPLOYMENT.md) for the backup and update procedure. It preserves Pocket’s current data directory and PM2 environment, keeps one Gunicorn worker on port 8768, and leaves your other apps alone. The examples assume `/home/kayo/apps/pocket`; substitute the actual Pocket checkout beneath `/home/kayo/apps`.

After backing up, the code update uses:

```bash
cd /home/kayo/apps/pocket
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -p 'test_*.py' -v
pm2 restart pocket
pm2 status pocket
pm2 logs pocket --lines 50 --nostream
```

Do not install `requirements-sync.txt` on the VPS; it is for the Mac helper only.

**Required Nginx change for Pocket’s site only:** preserve the bearer header. Replace the old empty `Authorization` override with:

```nginx
proxy_set_header Authorization $http_authorization;
```

The prepared `deploy/nginx-pocket-location.conf` includes this. Keep `auth_basic off;` for Pocket and retain the current certificates, HTTPS redirect, and forwarded-header settings. Do not log authorization headers or request/response bodies. Validate before reloading:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

A request to the sync endpoint without a token should return JSON `401`, with no browser credentials popup:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://pocket.mrox.in/api/obsidian/ledger.md
```

Log in as your existing owner account and open **Obsidian sync**. Generate the real token when ready to run Mac setup. It is shown once, and expires after 90 days. If you close it before saving, replace it. Never paste it into chat, an Obsidian note, or a shell command.

## Step 7: install and verify on the Mac — not performed yet

Use Python 3.9+ with macOS Keychain support. The helper uses the explicit [native macOS backend from keyring](https://keyring.readthedocs.io/en/latest/), without a plaintext fallback. Run installation commands in Terminal from the local Pocket checkout:

```bash
cd /Users/Rohit/Documents/Codex/2026-10-20/ma/outputs/expense-tracker
pocket_sync_dir="$HOME/Library/Application Support/pocket-obsidian-sync"
mkdir -p "$pocket_sync_dir"
chmod 700 "$pocket_sync_dir"
python3 -m venv "$pocket_sync_dir/venv"
"$pocket_sync_dir/venv/bin/python" -m pip install -r requirements-sync.txt
install -m 600 deploy/sync_obsidian.py "$pocket_sync_dir/sync_obsidian.py"
"$pocket_sync_dir/venv/bin/python" "$pocket_sync_dir/sync_obsidian.py" \
  --setup --url https://pocket.mrox.in \
  --vault "/Users/Rohit/Documents/Obsidian Vault"
```

Enter the token only at **Pocket sync token (hidden)**. Approve macOS Keychain access when requested. Setup stores the secret in Keychain, pins the returned owner UUID, and writes a non-secret `config.json` in the helper directory. No real ledger is changed during setup.

Verify a download without writing the note, then perform the first sync:

```bash
"$pocket_sync_dir/venv/bin/python" "$pocket_sync_dir/sync_obsidian.py" --dry-run
"$pocket_sync_dir/venv/bin/python" "$pocket_sync_dir/sync_obsidian.py"
```

Check `Expense Ledger.md` in Obsidian against your online records, including an expense, loan repayment, and reminder where present. The helper reports success only after reading the written note back. If a request, token, Keychain operation, or validation fails, it reports a safe error; the prior note remains available. A missing vault directory is not silently recreated.

The Keychain service name is `in.mrox.pocket.obsidian-sync:https://pocket.mrox.in`, with the owner UUID as the account name. Tokens never appear in config or logs. After replacing an expired/revoked token, rerun the same `--setup` command to update Keychain. Browser logout does not stop sync; **Revoke token** does.

## Step 8: schedule the verified helper — not performed yet

Once a manual sync succeeds, install a macOS LaunchAgent with:

- Label `in.mrox.pocket.obsidian-sync`.
- Program arguments: the absolute helper venv Python path, followed by the absolute helper script path above.
- `RunAtLoad` enabled and `StartInterval` set to `900` seconds.
- Standard output/error logs in the helper directory, with periodic log rotation.
- No token or password in the plist or its environment.

Save the plist under `~/Library/LaunchAgents/in.mrox.pocket.obsidian-sync.plist`, validate it with `plutil -lint`, and load it with `launchctl bootstrap gui/$(id -u) <absolute-plist-path>`. Verify with `launchctl print gui/$(id -u)/in.mrox.pocket.obsidian-sync` and a test record appearing in the vault.

This is a logged-in Mac service. It needs the Mac awake, online, and able to access Keychain and the vault. The website can remain available while the Mac is asleep, but Obsidian will catch up on a later successful run. See [Apple’s launchd guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| API 401 after setup | Token expiry/revocation, and Nginx forwarding `Authorization` |
| API 503 | Owner `POCKET_VAULT` export path and write permissions on the VPS |
| Redirect rejected | Use the final HTTPS domain; the helper deliberately never forwards a token through redirects |
| Keychain error | Unlock the login Keychain and authorize the helper’s Python executable; rerun manually first |
| Owner changed | Check whether you restored/replaced the server account database; do not bypass the identity check |
| Manual note/edits detected | Preserve your manual content separately before replacing the generated note |
| No automatic updates | A LaunchAgent has not been installed yet, or its job failed; inspect its safe logs |

Backups include the token hash through `accounts.sqlite3`; they never contain the original token. The restore command clears tokens to avoid reviving old credentials. Regenerate and configure a new token after restoring.
