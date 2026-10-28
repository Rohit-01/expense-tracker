# Summary filters and recoverable deletion

Implemented locally. No GitHub push, VPS update, or real-vault test writes were performed.

## Behavior

- Month and Trip selectors above the cards operate independently of the journal controls, Calculate total, reminder list, and entry form. Defaults are the current India-time month and All trips on the main page; trip pages default to All months with their trip fixed.
- All three cards use the original entry date. Spending is the expense amount; Money to collect is the current remaining loan balance; Open reminders is the current open status. These are not historical balance snapshots. Refreshes preserve the selected scope, including a month whose last entry has just been deleted.
- Delete opens a confirmation with the original record, amount where applicable, trip, repayment history impact, and attached-reminder impact. Confirming removes the active record graph in one transaction. Cancel makes no changes.
- Trash is available from the navigation. It shows the original record, trip, entry/deletion dates, notes, reminder state, and loan repayments. Restore returns original IDs, dates, assignments, and history. An overdue open reminder can become overdue again after restoration.
- There is no permanent deletion, automatic purge, deletion of individual repayments, or deletion of trip/mandate definitions. Imported calendar events stay in their calendar app.

## Mandate payments

A trashed mandate expense includes its original payment link. The month becomes unpaid and remains visible/payable even if the plan is paused. Other paused months remain paused; no future recurrence is resumed.

Restoration reinstates the original expense amount, date, and payment timestamp. A replacement payment for that month blocks restoration with an explanation. Users can move that replacement expense to Trash first when they want the original back. Database transactions and the existing unique mandate/month constraint prevent duplicate active payments.

## Data and interfaces

- `GET /trash`: authenticated page in production; same loopback-only single-user model as other local pages. Validated login and signup returns allow `/trash`.
- `POST /api/trash` with `{ "id": "entry UUID" }`: move an owned active entry with its repayments and any mandate-payment link. Repeating an already-completed move does not add another Trash record.
- `POST /api/restore` with the same body: restore an owned Trash record. Repeating a completed restoration does not duplicate active records. Both operations return updated account state and use the existing CSRF/origin protections.
- Each ledger gains an additive `trash(id, payload)` table. The saved bundle contains `id`, the original `entry`, `repayments`, optional `mandatePayment`, and `trashedAt`. Active entry, repayment, and payment-link rows move atomically. Failed moves/restores roll back together.
- State and JSON exports are schema version **4**, with a `trash` array. Mandate occurrences add a `paymentReversed` flag for an unpaid month reopened by Trash. Existing database backups copy the new table automatically.
- Missing active IDs prevent older clients from editing, repaying, or downloading calendar events for trashed records. Account identity still comes exclusively from the authenticated session, never a request parameter.
- Generated Markdown uses active records only. The owner-only Obsidian endpoint, sync token, vault configuration, and Mac scheduling remain unchanged. Pocket save confirmation does not claim the Mac has already synchronized.
- Forms/actions are disabled during submission. Failed or uncertain requests retain the current view and show an error; there is no automatic retry.

## Verification

Passed:

- Existing 70 Python regression tests plus 14 Trash tests: additive upgrades, both server modes, authentication/CSRF, owner-only output, other-account isolation, backup/restore, expenses/loans/reminders, partial/full repayments, stale edits, repeat/concurrent requests, paused mandate reversals, replacement conflicts, and transactional rollback.
- Existing 36 JavaScript tests plus 9 summary/Trash tests: combined scopes, empty totals, current balances, entry-date semantics, independent journal controls/drafts, preserved selections, confirmation cancellation, duplicate-click guards, uncertain failures, restoration conflicts, and responses arriving after logout.
- JavaScript/Python syntax checks, unique HTML IDs, matching static DOM references, and Git whitespace checks.

Rendered browser checks are **not verified**. Chromium failed before opening because the environment denied its macOS Mach-port registration. No visual or rendered keyboard pass is claimed.

The isolated browser scenario `tests/summary_trash_browser.cjs` checks the main page, trip page, Trash, popup, dialogs, keyboard focus, failures, and 320/390/480/768/1280-pixel widths. It uses disposable Flask data through a process pipe, never the real VPS or vault. With Playwright and a permitted browser environment, run from the project directory:

```sh
.venv/bin/python -m unittest discover -p 'test_*.py'
node --test test_*.js
POCKET_TEST_PYTHON="$PWD/.venv/bin/python" node tests/summary_trash_browser.cjs
```

Restart the local Pocket server and refresh to load the new backend and UI. Before a separately authorized VPS update, use the existing full database backup procedure and deploy the web files together. No new dependencies, sync credentials, or Nginx settings are required.
