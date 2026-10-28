# Pocket quick entry for Mac

Pocket is a menu-bar companion for **https://pocket.mrox.in**, supporting macOS 13+ on Apple Silicon and Intel. It opens a floating, resizable window over other apps. It is separate from the browser-installed web app and the existing local `Launch Pocket.command` server.

## Build and use locally

From the expense-tracker checkout, with Xcode and its command-line tools selected:

```sh
bash mac/build.sh release
open mac/build/release/Pocket.app
```

The build is ad-hoc signed for local testing. It is **not a public, Developer ID signed release**. It contains the native executable, icon, and manifest only; credentials, databases, vaults, and Python code are not bundled. Build outputs are ignored by Git.

The website changes must be deployed separately before the companion's entry page works. Until then, the app shows an unavailable message and an **Open dashboard** action. No VPS changes are made by building or opening the app.

1. Open Pocket and sign in inside its window. The browser and companion have separate login sessions.
2. Use **Control–Option–P** from another application, or choose **Quick entry** from the menu-bar icon.
3. Write `petrol for 200`, press **Command–Return**, review the draft, then press **Command–Shift–Return** or click Save.
4. After the server confirms, **Saved to Pocket** appears. The form clears, remains open, and focuses the sentence input.
5. **Escape** closes an open dialog first; otherwise it hides Pocket. Closing the window also hides it. Hiding preserves an unfinished draft in memory; quitting discards it.

Settings let you change the shortcut and opt into launch at login (off by default). The selectable keys use US physical key positions. An unavailable combination leaves the previous shortcut registered. Copy, paste, undo, and normal Tab navigation use standard Mac editing commands. Login startup uses `SMAppService` and may require approval in **System Settings → General → Login Items**; test the signed copy installed in Applications before release.

Pocket prevents repeated submissions while a save is pending. A lost connection may mean the server saved before the response was lost: **check the dashboard before retrying**. There is no automatic retry or offline queue. Quit is deferred while the form reports a pending save.

## Accounts, privacy, and Obsidian

- `/quick-entry` uses the existing dashboard markup, form handler, parser, categories, and authenticated APIs. It does not create another ledger. Parsing remains rule-based, without an AI service.
- Authenticated page redirects accept only `/` and `/quick-entry`. Existing seven-day absolute session expiry, CSRF protection, rate limits, and per-account ledgers remain in force.
- The companion loads only HTTPS Pocket login, sign-up, and quick-entry pages. User-clicked HTTPS links can open in the default browser. It does not accept arbitrary native commands or URLs from web content.
- WebKit uses a **nonpersistent store**. Only the validated `__Host-pocket-session` cookie is saved in macOS Keychain, under the app's bundle identifier plus `.session`. No passwords, financial records, local-storage data, offline caches, or drafts are persisted by the companion. Cookie restoration preserves Secure, HttpOnly, host, path, and expiry restrictions. Keychain access failures are shown in Settings and can require signing in again after quitting.
- On reopening, the native window covers the form until the website rechecks the account. Expired sessions or account changes clear the private form and return to authentication. Sleeping or leaving the macOS session hides the panel.
- Saves go to the VPS. Your existing `in.mrox.pocket.obsidian-sync` LaunchAgent separately downloads **only the owner's** generated Markdown using its own read-only token and configured vault. It normally runs every 15 minutes while the Mac is logged in, awake, connected, and able to access Keychain. The companion never reads that token or a vault path.
- `Saved to Pocket` confirms the server save, not completion of Obsidian sync. Other users' records never enter the owner's vault. Obsidian phone synchronization still depends on the user's separate vault-sync setup.

## Verification

Run from the expense-tracker directory, with the project's Python dependencies installed:

```sh
node --test test_*.js
python3 -m unittest discover -p 'test_*.py'
bash mac/build.sh debug
bash mac/test.sh
```

Native policy tests cover trusted URLs, cookie validation/restoration, the memory-only data store, shortcut validation, and the generated icon. Python tests include a compact-page expense flowing through the actual owner export endpoint and sync helper into a **temporary vault**, excluding a second account. JavaScript tests execute the shared submission handler and verify all four entry types, pending-request locking, draft retention on failure, authentication errors, and keyboard actions.

These additional checks need a normal logged-in Mac desktop session:

```sh
bash mac/test.sh --gui
# Install Playwright in your test environment, if it is not already available.
# POCKET_TEST_PYTHON must point to a Python with requirements.txt installed.
POCKET_TEST_PYTHON="/absolute/path/to/venv/bin/python" node tests/companion_browser.cjs
```

The browser test uses real page scripts and production Flask routes with a pipe transport, temporary accounts, and a temporary vault. No actual VPS is contacted. It checks login/signup/logout, all four entry types, duplicate submission, keyboard behavior, draft retention, 340/390/480/768/1280-pixel widths, and absence of browser storage/service workers. Set `POCKET_SCREENSHOT_DIR` to save screenshots; set `POCKET_BROWSER=webkit` to run against an installed Playwright WebKit engine.

**Implementation verification status:** terminal-based tests and universal compilation are verified. This coding environment denied GUI process startup, so the rendered browser checks, live global shortcut/conflict tests, real Keychain persistence across app relaunches, login-start behavior, and notarized installation still require a normal Mac desktop test. Do not treat compilation as verification of those interactions.

Manual release checks:

- Open/hide from another app using the default and a changed shortcut; try an occupied shortcut and verify the old one still works. Check multiple displays and full-screen Spaces.
- Sign in, quit/reopen, confirm the session persists; log out and reopen to confirm revocation. Expire/reset a test account's session, hide/show, and verify no stale form reappears.
- Save each entry kind, press Save repeatedly, disconnect during a save, and confirm no automatic resubmission. Resize the panel, Tab through fields, dismiss category dialogs, and verify unchanged dashboard behavior.
- Confirm other users' data cannot appear and the owner sync is correct. Use disposable test accounts and vaults for these checks.
- Test on an Intel Mac and Apple Silicon, macOS 13 and a current release, including launch at login from the signed Applications copy. No third-party keyboard monitoring or Accessibility permission is required by the implementation.

## Website rollout and public release

No GitHub push or VPS deployment is performed by this change. After separately approving and pushing the website update, use the existing [deployment instructions](../DEPLOYMENT.md) in the actual checkout below `/home/kayo/apps`, then restart only the Pocket PM2 process. No dependencies or schema migrations are added. Existing Nginx forwarding should pass `/quick-entry` and `/companion.js` to Gunicorn; preserve HTTPS. Verify signed-out `/quick-entry` redirects to `/login?next=/quick-entry` and sign-in returns to the form.

For public distribution:

1. Enroll in the Apple Developer Program and configure a **Developer ID Application** signing certificate. Do not put credentials, certificates, or private keys into this repository.
2. Configure a `notarytool` Keychain credential profile using Apple's documented process. Keep it in Keychain, never a script or chat.
3. Finish the desktop checks above; increment `CFBundleVersion` and `CFBundleShortVersionString` in `mac/Info.plist` for each release.
4. Run the following **only when authorizing uploads to Apple's notary service**:

   ```sh
   export POCKET_SIGNING_IDENTITY='Developer ID Application: Your Name (TEAMID)'
   export POCKET_NOTARY_PROFILE='pocket-notarization'
   bash mac/release.sh
   ```

   This builds the universal binary, enables hardened runtime, signs it, submits it to Apple, checks Accepted status, staples and validates the ticket, assesses Gatekeeper, then builds, signs, notarizes, and staples a DMG. It does not publish to GitHub or the website.
5. Install that DMG on a clean Mac and complete the final checks. Only then publish the verified artifact and add a website download link. No public download link is added prematurely.

Version 1 uses manual replacement downloads; it has no automatic updater. The account ledger stays on the server when replacing the Mac app. The old website/local launcher remain available independently.

References: [Apple direct distribution](https://developer.apple.com/macos/distribution), [notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution), [WebKit nonpersistent storage](https://developer.apple.com/documentation/webkit/wkwebsitedatastore), [login startup](https://developer.apple.com/documentation/servicemanagement/smappservice).
