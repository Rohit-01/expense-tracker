import AppKit
import WebKit

var checks = 0
func check(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard condition() else { fputs("FAIL: \(message)\n", stderr); exit(1) }
    checks += 1
}
check(PocketPolicy.embedded(URL(string: "https://pocket.mrox.in/quick-entry")!), "compact origin allowed")
for value in ["http://pocket.mrox.in/quick-entry", "https://evil.example/quick-entry", "https://pocket.mrox.in.evil.example/login",
              "https://user@pocket.mrox.in/login", "https://pocket.mrox.in:444/login", "file:///tmp/index.html", "https://pocket.mrox.in/api/export.json"] {
    check(!PocketPolicy.embedded(URL(string: value)!), "untrusted navigation rejected")
}
check(!PocketPolicy.external(URL(string: "javascript:alert(1)")!), "script URL rejected")
check(!PocketPolicy.external(URL(string: "file:///tmp/test")!), "file URL rejected")
check(Shortcut().label == "⌃⌥P", "default shortcut")
check(!Shortcut(control: false, option: false).valid, "unmodified shortcut rejected")
check(!Shortcut(key: "bad").valid, "unknown key rejected")
if CommandLine.arguments.contains("--gui") {
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory); app.finishLaunching()
    let first = GlobalShortcut(), second = GlobalShortcut()
    let registered = first.register(Shortcut(key: "9", control: true, option: true, command: true, shift: true))
    check(registered, "hotkey registration (OSStatus \(first.lastStatus))")
    check(!second.register(Shortcut(key: "9", control: true, option: true, command: true, shift: true)), "hotkey conflicts rejected")
    check(first.current?.key == "9", "old hotkey retained")
}
let cookie = HTTPCookie(properties: [.name: SessionCookie.name, .value: String(repeating: "a", count: 43),
    .domain: "pocket.mrox.in", .path: "/", .secure: "TRUE", .expires: Date().addingTimeInterval(3600), HTTPCookiePropertyKey("HttpOnly"): "TRUE"])!
let session = SessionCookie(cookie)
check(session != nil, "secure session can persist")
check(session?.cookie()?.isHTTPOnly == true, "restored cookie remains HttpOnly")
check(session?.cookie()?.isSecure == true, "restored cookie remains Secure")
var unsafe = cookie.properties!; unsafe[.domain] = "evil.example"
check(SessionCookie(HTTPCookie(properties: unsafe)!) == nil, "foreign cookies never persist")
unsafe = cookie.properties!; unsafe[.expires] = Date().addingTimeInterval(-100)
check(SessionCookie(HTTPCookie(properties: unsafe)!) == nil, "expired cookie rejected")
let data = try! JSONEncoder().encode(session!)
check((try! JSONDecoder().decode(SessionCookie.self, from: data)).cookie() != nil, "session restoration")
check(!WKWebsiteDataStore.nonPersistent().isPersistent, "no disk web storage")
check(NSImage(contentsOfFile: "mac/build/debug/Pocket.app/Contents/Resources/Pocket.icns") != nil, "app icon decodes")
print("\(checks) native checks passed. Live hotkeys require --gui in a logged-in desktop session.")
