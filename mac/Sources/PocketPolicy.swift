import Foundation

enum PocketPolicy {
    static let productionOrigin = URL(string: "https://pocket.mrox.in")!
    static var origin: URL {
        #if DEBUG
        // Local fixtures only. Release builds cannot override the production server.
        if let raw = ProcessInfo.processInfo.environment["POCKET_TEST_ORIGIN"],
           let url = URL(string: raw), url.scheme == "https", url.host == "localhost",
           url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
           url.path.isEmpty, url.port != nil { return url }
        #endif
        return productionOrigin
    }
    static var quickEntry: URL { origin.appendingPathComponent("quick-entry") }
    static func sameOrigin(_ url: URL) -> Bool {
        url.scheme == "https" && url.host == origin.host && (url.port ?? 443) == (origin.port ?? 443)
            && url.user == nil && url.password == nil
    }
    static func embedded(_ url: URL) -> Bool {
        sameOrigin(url) && ["/quick-entry", "/login", "/signup"].contains(url.path)
    }
    static func external(_ url: URL) -> Bool {
        url.scheme == "https" && url.host != nil && url.user == nil && url.password == nil
    }
}
