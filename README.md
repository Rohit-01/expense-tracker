# LifeOS Pocket

A private expense tracker for local use or a VPS with separate accounts. Record expenses, money lent, partial repayments, and dated reminders. Currency is INR; reminder times use Asia/Kolkata.

For GitHub → VPS deployment with HTTPS, password protection, automatic restarts, and backups, follow [DEPLOYMENT.md](DEPLOYMENT.md). The separate production entry point is `production:create_app()`, run with Gunicorn; the Mac launcher below remains for local use.

For a floating Mac entry window with **Control–Option–P**, see the [Mac companion setup and release guide](mac/README.md). It uses your online Pocket account; the website's `/quick-entry` update must be deployed before the companion can save. Local builds are available before public signing/notarization.

## Open

Double-click **Launch Pocket.command**. It opens `http://127.0.0.1:8768` in your browser. Keep its Terminal window open while using Pocket. Closing it stops the app, but your records stay saved. If macOS asks which app should open the launcher, choose Terminal.

The tracker is also usable in the Codex browser panel while the server is running. Keep the entire expense-tracker folder together. Opening index.html directly will not start the tracker.

## Use

1. Write a short sentence, such as `Bought headphones for ₹2,500`, `200 petrol`, `petrol for rupees 200`, or `Lent ₹5,000 to Rahul`, and select **Fill details**. Or fill the form directly. Quick entry accepts amounts before or after an item, common rupee notations, and `k`/`lakh`/`crore` shorthand.
2. Review the suggested values, then save. Quick entry uses local phrase matching, not AI. If it finds multiple amounts or cannot confidently identify one, it leaves Amount empty for you to enter. Always check the amount, person, and dates. Supported date phrases are today, tomorrow, and YYYY-MM-DD; unspecified reminder times default to 9 AM and are flagged for review.
3. Use **Record repayment** only when money has actually been received. Partial repayments are supported, and the outstanding amount updates automatically. Loans and repayments are separate from spending totals.
4. Add a reminder date to an expense or loan, or create a standalone reminder. **On your radar** shows open reminders. Marking a reminder done does not settle a loan.
5. To receive a calendar alert, download **Calendar event ↗**, open the .ics file in your calendar, and confirm its import. The event has an alert at the selected time. Calendar notifications depend on your calendar app's settings. An imported event is independent: later tracker edits, repayment, or completion do not update or remove it from your calendar.
6. Use **Add category or subcategory** in the expense form to create your own labels. For example, add **Diet Coke** under **Food & groceries**. Quick entry then fills that subcategory for `Spent 200 rupees on diet coke`; review the draft before saving. Matching is local phrase matching, not AI. A unique subcategory match takes priority over an explicitly named category, then common purchase keywords (for example, pizza selects Food & groceries). Unknown or conflicting categories fall back to Other with a review hint. If you change the sentence after filling the draft, Pocket asks you to click **Fill details** again; typing does not overwrite your manual field edits.
7. Choose **Mandate** in **Write it down** to record a monthly plan such as a subscription or mutual fund contribution. Enter its name, amount, first due date, and category. Select **Mandates** beside **Reminders** in the journal tabs to see current and overdue months, or add another plan. Click **Paid** only after a real payment: Pocket records one expense dated today in India time and leaves a green paid marker in that month's row. Pocket never initiates a charge. Edit, pause, or resume the overall plan using its separate controls. A date on the 29th–31st uses the last day of shorter months.
8. Filter **Your journal** by type, month, trip, and search together. **Calculate total** adds only the visible records: expenses use their recorded amounts, loans use their outstanding balances, standalone reminders add zero, and mandates use each visible monthly occurrence. Use **Mark repaid** only after receiving the full outstanding loan amount; Pocket records that repayment today and keeps the completed loan and its history under **Money lent**.

Pocket does not send background notifications or messages. It does not create calendar events without your calendar import. Nothing is scheduled merely by trying an example.

## Trips

Choose **Trips** beside **Journal**, then **Create trip**. Only the name is required; destination and travel dates are optional. Open a trip card for its spending across all dates, outstanding loans, reminders, shared entry form, and filtered journal. Travel dates do not restrict which records belong to a trip.

- **Make active** sets this account’s default on every device. A fresh form on the main page or Mac popup preselects it. Inside a trip, a fresh form preselects that trip. Review the visible **Trip** field before saving; choose **No trip** whenever needed. An existing draft keeps its selection even when another device changes the active trip.
- **Stop using as default** clears the default. **Mark completed** also clears it if active. Completed trips remain editable for late receipts and repayments; **Reopen trip** restores open status without automatically making it active. Completion does not repay loans or finish reminders.
- **Edit** on an existing record lets you assign, move, or remove its trip. Each record exists once: both the main journal and its trip display it. Summary filters use the recorded entry date. Trip summaries default to all months; loans contribute only to money to collect.
- The main journal’s **All trips / No trip / named trip** selector combines with search, month, type, and **Calculate total**. Linked records have a trip badge. Mandates have no trip or default assignment; an expense generated by **Paid** can be assigned through Edit afterward.
- Trip pages offer expenses, money lent, and reminders; they have no mandate tab or creation option. There is no trip deletion in this version.

Trips are stored in the same private account ledger. The additive upgrade runs automatically when the ledger opens; existing records remain unassigned. Old clients preserve an assignment when editing without sending `tripId`, and create unassigned records. JSON schema version 4 includes trips, the active default, and Trash; SQLite backups capture the new tables automatically.

Generated Obsidian notes include trip labels and a trip summary. The owner-only sync endpoint, token, vault path, and Mac schedule stay the same. A successful Pocket save does not mean the Mac has synchronized yet. See [TRIPS.md](TRIPS.md) for implementation and verification details.

## Summary filters and Trash

Use **Month** and **Trip** above the three summary cards. The main dashboard starts with **This month · All trips**; a trip page starts with **All months** and that trip fixed. All three cards filter by the record’s entry date. Money to collect uses the current remaining balance, and Open reminders uses the current status—these are not historical month-end balances. Journal filters, Calculate total, the reminder list, and unfinished entry drafts remain independent.

**Delete** beside Edit opens a confirmation. Confirming moves the record out of journals, totals, reminders, and the generated Obsidian note, into **Trash** beside Journal and Trips. The vault changes on its next successful sync. Choose **Restore** to bring back the original dates, trip, reminder state, and full loan repayment history. Trash is kept indefinitely; there is no permanent-delete or automatic-purge control. JSON exports and database backups include Trash.

Deleting a mandate-generated expense returns its month to unpaid, including when the plan is paused, without resuming future months. Restoring it marks that month paid with its original payment details. If a replacement payment already exists, Pocket refuses restoration; move the replacement expense to Trash first if you intend to restore the original. Imported calendar events are managed separately in your calendar app.

See [SUMMARY_TRASH.md](SUMMARY_TRASH.md) for storage, compatibility, and verification notes.

## Local records and Obsidian

- Original data: `data/ledger.sqlite3` next to server.py. Records survive refreshes and restarts.
- Automatic Markdown view: `ai zone/openai agent/Personal/Finance/Expense Ledger.md` inside the configured Obsidian vault. The app reads it back after each write and shows whether syncing succeeded.
- Edit entries through Pocket; the generated ledger note is overwritten on sync. Manual notes elsewhere in your vault are not modified.
- If vault syncing fails, data remains saved locally and the app shows a warning.
- **Export backup** downloads JSON containing entries, repayments, categories, mandates and their monthly payment history, trips, entry assignments, the active-trip setting, and recoverable Trash, with amounts in integer paise (`amountMinor`). Keep a copy somewhere safe. This version has JSON export but no import interface; a backup can be used to recover records with assistance.
- No cloud service, bank connection, account, or external network service is used. Data and the Obsidian view are ordinary local files, not encrypted by this app. Only this Mac can connect to the loopback server.

To correct an entry, use **Edit**. Use **Delete** to move an expense, loan, or reminder into **Trash**, where it can be restored. Loans move with their full repayment history; individual repayments remain append-only. Trips and mandate definitions cannot be deleted.

## Local technical details

Python 3.9+ with its standard library is required. No packages need installing. Start manually with `python3 server.py`. The Mac's bundled setup used `/opt/homebrew/bin/python3`.

Vault path is stored in `config.json`. Set `vault` to an empty string to disable syncing. For isolated tests, use `python3 server.py --port 8769 --data-dir /path/to/test-data --vault ''`.

Changing the port also requires using the corresponding URL; the double-click launcher defaults to 8768. The server uses strict Host checks, a per-run anti-forgery token for mutations, parameterized SQL, integer monetary values, and escaped Markdown/user text.

## Public accounts on the VPS

The production adapter now provides `/login`, `/signup`, and logout using persistent SQLite sessions. Your old `auth.json` login is imported once as the owner of the existing ledger. Each public signup receives a separate empty ledger; only the owner's records write to `POCKET_VAULT`. The local Mac launcher retains its single-user behavior.

## Add Pocket to a phone's Home Screen

After the updated code is deployed to `https://pocket.mrox.in`, users can open the site on a phone and save Pocket as a home-screen **app icon**. On iPhone, use Safari’s **Share → Add to Home Screen**, turn on **Open as Web App**, then tap **Add**. On Android, use Chrome’s **⋮ → Install app** or **Add to Home screen**. The exact menu wording depends on the browser. Tap the Pocket icon to open the same online account; sign in there if the session has expired.

Pocket provides the install manifest and branded icons on both the login and dashboard pages. The installed experience opens at `/`, then uses the normal login flow. It is an online web app, so the VPS and internet connection are required. It is not a live data widget, an App Store/Play Store download, or an offline copy of financial records. No service worker caches private pages or account data.

See [DEPLOYMENT.md](DEPLOYMENT.md) for the PM2/Nginx update, backup, migration, password reset, and restore steps. The new PM2 and Nginx examples are in `deploy/`. Do not commit actual databases, exports, credentials, or machine configuration.

Run account/privacy checks in the production virtual environment:

```bash
.venv/bin/python -m unittest discover -p 'test_*.py' -v
node --test test_*.js
```

## Sync your online owner account to Obsidian

The production dashboard now has an owner-only **Obsidian sync** control. Generate a read-only token there after deploying this release. It expires after 90 days; replacing or revoking it blocks future downloads with that token. Pocket stores only its hash.

The prepared Mac helper uses HTTPS and macOS Keychain to update only `ai zone/openai agent/Personal/Finance/Expense Ledger.md`. It validates the owner and generated Markdown before an atomic write, preserves the note on connection failures, and detects manual edits to a previously synced note. It does not upload local edits or need an SSH login. The local Pocket launcher stays single-user and does not overwrite a note managed by the VPS sync helper.

See [OBSIDIAN_SYNC.md](OBSIDIAN_SYNC.md). Deployment, real token setup, and the periodic LaunchAgent are separate remaining steps; the code alone does not make your Mac sync automatically.
