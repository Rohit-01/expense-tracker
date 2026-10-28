import Foundation
import Security
import WebKit

// WebKit is memory-only. The one HttpOnly session cookie is the only web data
// persisted, in Keychain. No passwords, records, drafts, or Obsidian tokens.
struct SessionCookie: Codable {
    let value: String
    let expires: Date
    static let name = "__Host-pocket-session"
    init?(_ cookie: HTTPCookie) {
        guard cookie.name == Self.name, cookie.domain == PocketPolicy.origin.host,
              cookie.path == "/", cookie.isSecure, cookie.isHTTPOnly,
              let expiry = cookie.expiresDate, expiry > Date(),
              expiry.timeIntervalSinceNow <= 7 * 86400 + 60,
              cookie.value.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil else { return nil }
        value = cookie.value; expires = expiry
    }
    func cookie() -> HTTPCookie? {
        guard expires > Date(), expires.timeIntervalSinceNow <= 7 * 86400 + 60,
              value.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil else { return nil }
        return HTTPCookie(properties: [.name: Self.name, .value: value,
            .domain: PocketPolicy.origin.host!, .path: "/", .secure: "TRUE", .expires: expires,
            HTTPCookiePropertyKey("HttpOnly"): "TRUE", HTTPCookiePropertyKey("SameSite"): "Lax"])
    }
}

final class SessionVault: NSObject, WKHTTPCookieStoreObserver {
    private let store: WKHTTPCookieStore
    private var observing = false
    var onFailure: (() -> Void)?
    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: (Bundle.main.bundleIdentifier ?? "in.mrox.pocket.quick-entry") + ".session",
         kSecAttrAccount as String: PocketPolicy.origin.absoluteString]
    }
    init(store: WKHTTPCookieStore) { self.store = store }
    func restore(completion: @escaping () -> Void) {
        var lookup = query
        lookup[kSecReturnData as String] = true
        lookup[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(lookup as CFDictionary, &result)
        if status == errSecSuccess, let data = result as? Data,
           let session = try? JSONDecoder().decode(SessionCookie.self, from: data), let cookie = session.cookie() {
            store.setCookie(cookie) { self.observe(); completion() }
        } else {
            if status == errSecSuccess { _ = SecItemDelete(query as CFDictionary) }
            if status != errSecSuccess && status != errSecItemNotFound { onFailure?() }
            observe(); completion()
        }
    }
    private func observe() { if !observing { store.add(self); observing = true } }
    func cookiesDidChange(in cookieStore: WKHTTPCookieStore) { persist() }
    func persist(completion: @escaping () -> Void = {}) {
        store.getAllCookies { cookies in
            let cookie = cookies.first { $0.name == SessionCookie.name && $0.domain == PocketPolicy.origin.host }
            if let cookie, let session = SessionCookie(cookie), let data = try? JSONEncoder().encode(session) {
                let attributes: [String: Any] = [kSecValueData as String: data]
                var status = SecItemUpdate(self.query as CFDictionary, attributes as CFDictionary)
                if status == errSecItemNotFound {
                    var item = self.query.merging(attributes) { _, new in new }
                    item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
                    status = SecItemAdd(item as CFDictionary, nil)
                }
                if status != errSecSuccess { self.onFailure?() }
            } else {
                let status = SecItemDelete(self.query as CFDictionary)
                if status != errSecSuccess && status != errSecItemNotFound { self.onFailure?() }
            }
            completion()
        }
    }
}
