import AppKit
import Carbon

struct Shortcut: Codable, Equatable {
    var key: String = "P"
    var control = true
    var option = true
    var command = false
    var shift = false
    static let keys: [String: UInt32] = ["A":0,"S":1,"D":2,"F":3,"H":4,"G":5,"Z":6,"X":7,"C":8,"V":9,
        "B":11,"Q":12,"W":13,"E":14,"R":15,"Y":16,"T":17,"1":18,"2":19,"3":20,"4":21,"6":22,
        "5":23,"9":25,"7":26,"8":28,"0":29,"O":31,"U":32,"I":34,"P":35,"L":37,"J":38,"K":40,"N":45,"M":46]
    var modifiers: UInt32 {
        (control ? UInt32(controlKey) : 0) | (option ? UInt32(optionKey) : 0)
            | (command ? UInt32(cmdKey) : 0) | (shift ? UInt32(shiftKey) : 0)
    }
    var valid: Bool { Self.keys[key] != nil && (control || option || command) }
    var label: String { (control ? "⌃" : "") + (option ? "⌥" : "") + (shift ? "⇧" : "") + (command ? "⌘" : "") + key }
}

final class GlobalShortcut {
    private var reference: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private(set) var current: Shortcut?
    private(set) var lastStatus: OSStatus = noErr
    var onPress: (() -> Void)?
    init() {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        lastStatus = InstallEventHandler(GetApplicationEventTarget(), { _, _, context in
            guard let context else { return OSStatus(eventNotHandledErr) }
            Unmanaged<GlobalShortcut>.fromOpaque(context).takeUnretainedValue().onPress?()
            return noErr
        }, 1, &spec, Unmanaged.passUnretained(self).toOpaque(), &handler)
    }
    // Register the replacement first: a failed change must keep the old shortcut.
    @discardableResult func register(_ shortcut: Shortcut) -> Bool {
        guard shortcut.valid, handler != nil else { return false }
        if current == shortcut && reference != nil { return true }
        var replacement: EventHotKeyRef?
        let identity = EventHotKeyID(signature: 0x506f636b, id: 1)
        let status = RegisterEventHotKey(Shortcut.keys[shortcut.key]!, shortcut.modifiers, identity,
                                        GetApplicationEventTarget(), 0, &replacement)
        lastStatus = status
        guard status == noErr, replacement != nil else { return false }
        if let reference { UnregisterEventHotKey(reference) }
        reference = replacement; current = shortcut
        return true
    }
    deinit {
        if let reference { UnregisterEventHotKey(reference) }
        if let handler { RemoveEventHandler(handler) }
    }
}
