#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p mac/build/tests/cache
xcrun swiftc -swift-version 5 -module-cache-path mac/build/tests/cache \
  mac/Sources/PocketPolicy.swift mac/Sources/SessionCookie.swift mac/Sources/GlobalShortcut.swift mac/Tests/main.swift \
  -o mac/build/tests/PocketTests -framework AppKit -framework WebKit -framework Carbon -framework Security
mac/build/tests/PocketTests "$@"
