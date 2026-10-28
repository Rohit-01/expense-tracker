import AppKit
import WebKit
import ServiceManagement

final class PocketPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    private(set) var panel: PocketPanel!
    private(set) var webView: WKWebView!
    private var vault: SessionVault!
    private var statusItem: NSStatusItem!
    private var quickItem: NSMenuItem!
    private let hotkey = GlobalShortcut()
    private var settings: NSWindow?
    private var keyChoice: NSPopUpButton!
    private var modifiers: [NSButton] = []
    private var settingsStatus: NSTextField!
    private var loginToggle: NSButton!
    private var cover: NSStackView!
    private var coverText: NSTextField!
    private var retry: NSButton!
    private var ready = false
    private var restored = false
    private var visibilityVersion = 0
    private var terminating = false
    private var sessionWarning = false
    private var awaitingLogout = false
    #if DEBUG
    var disableKeychainForTests = ProcessInfo.processInfo.environment["POCKET_TEST_NO_KEYCHAIN"] == "1"
    #endif

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        URLCache.shared = URLCache(memoryCapacity: 0, diskCapacity: 0, diskPath: nil)
        createMenu()
        createPanel()
        let saved = UserDefaults.standard.data(forKey: "globalShortcut").flatMap { try? JSONDecoder().decode(Shortcut.self, from: $0) }
        let shortcut = saved ?? Shortcut()
        hotkey.onPress = { [weak self] in self?.togglePanel() }
        if hotkey.register(shortcut) { quickItem.title = "Quick entry  \(shortcut.label)" }
        else { showSettings(message: "The shortcut could not be registered. It may be in use or unavailable in this session. Choose another combination; Pocket is still available from the menu bar.") }
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(hidePanel), name: NSWorkspace.sessionDidResignActiveNotification, object: nil)
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(hidePanel), name: NSWorkspace.willSleepNotification, object: nil)
        // A login-started app stays in the menu bar; manual launches open the panel.
        if NSAppleEventManager.shared().currentAppleEvent?.paramDescriptor(forKeyword: keyAEPropData)?.enumCodeValue != keyAELaunchedAsLogInItem {
            showPanel()
        }
    }

    private func createMenu() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = NSImage(systemSymbolName: "p.square.fill", accessibilityDescription: "Pocket quick entry")
        let menu = NSMenu()
        quickItem = add(menu, "Quick entry", #selector(togglePanel))
        add(menu, "Open dashboard", #selector(openDashboard))
        menu.addItem(.separator())
        add(menu, "Settings…", #selector(openSettings))
        add(menu, "Log out", #selector(logout))
        menu.addItem(.separator())
        add(menu, "Quit Pocket", #selector(quit), "q")
        statusItem.menu = menu
        // Standard Edit menu preserves paste, copy, select all, and undo in WKWebView.
        let main = NSMenu(), application = NSMenu(), edit = NSMenu(title: "Edit")
        add(application, "Quit Pocket", #selector(quit), "q")
        for (title, action, key) in [("Undo", "undo:", "z"), ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: NSSelectorFromString(action), keyEquivalent: key)
        }
        let appItem = NSMenuItem(); appItem.submenu = application; main.addItem(appItem)
        let editItem = NSMenuItem(); editItem.submenu = edit; main.addItem(editItem)
        NSApp.mainMenu = main
    }
    @discardableResult private func add(_ menu: NSMenu, _ title: String, _ action: Selector, _ key: String = "") -> NSMenuItem {
        let item = menu.addItem(withTitle: title, action: action, keyEquivalent: key); item.target = self; return item
    }

    private func createPanel() {
        panel = PocketPanel(contentRect: NSRect(x: 0, y: 0, width: 480, height: 720),
                            styleMask: [.titled, .closable, .resizable, .utilityWindow], backing: .buffered, defer: false)
        panel.title = "Pocket · Quick entry"; panel.delegate = self
        panel.isFloatingPanel = true; panel.level = .floating; panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isReleasedWhenClosed = false; panel.isRestorable = false
        panel.minSize = NSSize(width: 340, height: 420); panel.center()
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.applicationNameForUserAgent = "PocketMac/1.0"
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        config.userContentController.add(self, name: "pocket")
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self; webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        webView.translatesAutoresizingMaskIntoConstraints = false
        let content = NSView(); panel.contentView = content
        content.addSubview(webView)
        NSLayoutConstraint.activate([webView.leadingAnchor.constraint(equalTo: content.leadingAnchor), webView.trailingAnchor.constraint(equalTo: content.trailingAnchor), webView.topAnchor.constraint(equalTo: content.topAnchor), webView.bottomAnchor.constraint(equalTo: content.bottomAnchor)])
        coverText = NSTextField(wrappingLabelWithString: "Connecting to Pocket…")
        coverText.alignment = .center
        retry = NSButton(title: "Retry", target: self, action: #selector(retryConnection))
        let dashboard = NSButton(title: "Open dashboard", target: self, action: #selector(openDashboard))
        cover = NSStackView(views: [coverText, retry, dashboard]); cover.orientation = .vertical; cover.spacing = 16
        cover.translatesAutoresizingMaskIntoConstraints = false; content.addSubview(cover)
        NSLayoutConstraint.activate([cover.centerXAnchor.constraint(equalTo: content.centerXAnchor), cover.centerYAnchor.constraint(equalTo: content.centerYAnchor), cover.widthAnchor.constraint(lessThanOrEqualTo: content.widthAnchor, constant: -48)])
        showCover("Connecting to Pocket…", retryable: false)
        vault = SessionVault(store: config.websiteDataStore.httpCookieStore)
        vault.onFailure = { [weak self] in self?.sessionWarning = true }
        #if DEBUG
        if disableKeychainForTests { restored = true; loadEntry(); return }
        #endif
        vault.restore { [weak self] in self?.restored = true; self?.loadEntry() }
    }
    private func showCover(_ message: String, retryable: Bool = true) {
        webView.isHidden = true; cover.isHidden = false; coverText.stringValue = message; retry.isHidden = !retryable
    }
    private func reveal() {
        guard panel.isVisible else { return }
        cover.isHidden = true; webView.isHidden = false; panel.makeFirstResponder(webView)
    }
    private func loadEntry() {
        ready = false; showCover("Connecting to Pocket…", retryable: false)
        webView.load(URLRequest(url: PocketPolicy.quickEntry, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: 25))
    }
    @objc func togglePanel() { panel.isVisible ? hidePanel() : showPanel() }
    func showPanel() {
        visibilityVersion += 1
        showCover("Checking your session…", retryable: false)
        NSApp.activate(ignoringOtherApps: true); panel.makeKeyAndOrderFront(nil)
        if ready { verifySession() }
        else if restored && !webView.isLoading { loadEntry() }
    }
    @objc func hidePanel() {
        visibilityVersion += 1; webView.isHidden = true; panel.orderOut(nil)
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if sender === panel { hidePanel(); return false }
        return true
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showPanel(); return true }
    @objc private func retryConnection() { if restored { ready ? verifySession() : loadEntry() } }
    private func verifySession() {
        let version = visibilityVersion
        showCover("Checking your session…", retryable: false)
        webView.callAsyncJavaScript("return window.pocketCompanion?.version === 1 && await window.pocketCompanion.resume();", arguments: [:], in: nil, in: .page) { [weak self] result in
            guard let self, version == self.visibilityVersion else { return }
            if case .success(let value) = result, value as? Bool == true, self.ready, !self.webView.isLoading { self.reveal() }
            else if !self.webView.isLoading { self.showCover("Could not confirm your session. Connect to the internet, then retry.") }
        }
    }
    @objc func openDashboard() { NSWorkspace.shared.open(PocketPolicy.origin) }
    @objc func logout() {
        guard !awaitingLogout else { return }
        showPanel()
        guard ready else { showCover("Connect to Pocket before logging out. Retry, then choose Log out again."); return }
        awaitingLogout = true
        webView.callAsyncJavaScript("return await window.pocketCompanion?.logout();", arguments: [:], in: nil, in: .page) { [weak self] result in
            guard let self else { return }; self.awaitingLogout = false
            // The website revokes the server session before deleting its cookie.
            if case .success(let success) = result, success as? Bool == true { return }
            if !self.webView.isLoading { self.reveal() }
        }
    }
    @objc private func quit() { NSApp.terminate(nil) }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if terminating || !restored { return .terminateCancel }
        terminating = true
        webView.evaluateJavaScript("window.pocketCompanion?.isSaving() === true") { [weak self] value, _ in
            guard let self else { NSApp.reply(toApplicationShouldTerminate: true); return }
            if value as? Bool == true {
                self.terminating = false; self.showPanel()
                NSApp.reply(toApplicationShouldTerminate: false)
                let alert = NSAlert(); alert.messageText = "A save is still in progress"
                alert.informativeText = "Wait for Pocket to confirm the save before quitting."
                alert.beginSheetModal(for: self.panel)
                return
            }
            self.hidePanel()
            #if DEBUG
            if self.disableKeychainForTests { NSApp.reply(toApplicationShouldTerminate: true); return }
            #endif
            self.vault.persist { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let url = message.frameInfo.request.url,
              PocketPolicy.embedded(url), message.name == "pocket", message.body as? String == "hide" else { return }
        hidePanel()
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if navigationAction.targetFrame?.isMainFrame == true && PocketPolicy.embedded(url) { decisionHandler(.allow); return }
        if navigationAction.navigationType == .linkActivated && PocketPolicy.external(url) { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard navigationResponse.isForMainFrame,
              let response = navigationResponse.response as? HTTPURLResponse,
              let url = response.url, PocketPolicy.embedded(url), response.statusCode == 200,
              response.mimeType == "text/html" else {
            ready = false
            showCover("Quick entry is unavailable. The website may need the companion update deployed. You can use the dashboard meanwhile.")
            decisionHandler(.cancel); return
        }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        visibilityVersion += 1; ready = false; showCover("Connecting to Pocket…", retryable: false)
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        ready = true; if panel.isVisible { verifySession() }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { navigationFailed(error) }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { navigationFailed(error) }
    private func navigationFailed(_ error: Error) {
        if (error as NSError).code == NSURLErrorCancelled { return }
        if (error as NSError).domain == "WebKitErrorDomain" && (error as NSError).code == 102 { return }
        ready = false; showCover("Could not securely reach Pocket. Check your connection, then retry.")
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        ready = false; showCover("The entry window restarted. An unsaved draft may have been lost. Check your journal before entering a payment again.")
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? { nil }
    // TLS challenges use WebKit's system trust evaluation. No certificate bypass.

    @objc private func openSettings() { showSettings() }
    private func showSettings(message: String = "") {
        if settings == nil {
            settings = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 430, height: 320), styleMask: [.titled, .closable], backing: .buffered, defer: false)
            settings!.title = "Pocket settings"; settings!.isReleasedWhenClosed = false; settings!.center()
            let title = NSTextField(labelWithString: "Global quick-entry shortcut")
            keyChoice = NSPopUpButton(); keyChoice.addItems(withTitles: Shortcut.keys.keys.sorted())
            modifiers = ["Control ⌃", "Option ⌥", "Command ⌘", "Shift ⇧"].map { NSButton(checkboxWithTitle: $0, target: nil, action: nil) }
            let choices = NSStackView(views: [modifiers[0], modifiers[1], keyChoice]); choices.spacing = 12
            let extraModifiers = NSStackView(views: [modifiers[2], modifiers[3]]); extraModifiers.spacing = 12
            let apply = NSButton(title: "Apply shortcut", target: self, action: #selector(applyShortcut))
            loginToggle = NSButton(checkboxWithTitle: "Launch Pocket at login", target: self, action: #selector(changeLoginStartup))
            settingsStatus = NSTextField(wrappingLabelWithString: "")
            let help = NSTextField(wrappingLabelWithString: "⌘ Return: fill details. ⌘ Shift Return: save. Esc: close a dialog or hide Pocket. Entries require an internet connection.")
            let stack = NSStackView(views: [title, choices, extraModifiers, apply, loginToggle, help, settingsStatus]); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 12
            stack.translatesAutoresizingMaskIntoConstraints = false; settings!.contentView!.addSubview(stack)
            NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: settings!.contentView!.leadingAnchor, constant: 24), stack.trailingAnchor.constraint(equalTo: settings!.contentView!.trailingAnchor, constant: -24), stack.topAnchor.constraint(equalTo: settings!.contentView!.topAnchor, constant: 24)])
        }
        let shortcut = hotkey.current ?? Shortcut()
        keyChoice.selectItem(withTitle: shortcut.key)
        for (button, enabled) in zip(modifiers, [shortcut.control, shortcut.option, shortcut.command, shortcut.shift]) { button.state = enabled ? .on : .off }
        loginToggle.state = SMAppService.mainApp.status == .enabled ? .on : .off
        settingsStatus.stringValue = !message.isEmpty ? message : sessionWarning ? "Keychain could not preserve your session. You may need to sign in again after quitting." : ""
        NSApp.activate(ignoringOtherApps: true); settings!.makeKeyAndOrderFront(nil)
    }
    @objc private func applyShortcut() {
        let shortcut = Shortcut(key: keyChoice.titleOfSelectedItem ?? "P", control: modifiers[0].state == .on, option: modifiers[1].state == .on, command: modifiers[2].state == .on, shift: modifiers[3].state == .on)
        guard shortcut.valid else { settingsStatus.stringValue = "Include Control, Option, or Command."; return }
        guard hotkey.register(shortcut) else { settingsStatus.stringValue = "That shortcut is unavailable. Choose another; your previous shortcut is unchanged."; return }
        UserDefaults.standard.set(try? JSONEncoder().encode(shortcut), forKey: "globalShortcut")
        quickItem.title = "Quick entry  \(shortcut.label)"; settingsStatus.stringValue = "Shortcut saved."
    }
    @objc private func changeLoginStartup() {
        do {
            if loginToggle.state == .on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            loginToggle.state = SMAppService.mainApp.status == .enabled ? .on : .off
            settingsStatus.stringValue = SMAppService.mainApp.status == .requiresApproval ? "Approve Pocket in System Settings → General → Login Items." : "Login startup setting updated."
        } catch {
            loginToggle.state = SMAppService.mainApp.status == .enabled ? .on : .off
            settingsStatus.stringValue = "Could not change login startup. Install the signed app in Applications and check System Settings → General → Login Items."
        }
    }
}
