#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
mode="${1:-release}"
if [[ "$mode" != release && "$mode" != debug ]]; then
  echo 'Usage: bash mac/build.sh [release|debug]' >&2; exit 1
fi
build="$PWD/build/$mode"
bundle="$build/Pocket.app"
mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources" "$build/cache" "$build/Pocket.iconset"
cp Info.plist "$bundle/Contents/Info.plist"
flags=()
if [[ "$mode" == debug ]]; then
  flags=(-D DEBUG -g)
  /usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier in.mrox.pocket.quick-entry.debug' "$bundle/Contents/Info.plist"
else
  flags=(-O)
fi
for arch in arm64 x86_64; do
  xcrun swiftc -swift-version 5 -target "$arch-apple-macos13.0" -module-cache-path "$build/cache" \
    "${flags[@]}" Sources/*.swift -o "$build/Pocket-$arch" \
    -framework AppKit -framework WebKit -framework Carbon -framework ServiceManagement -framework Security
done
xcrun lipo -create "$build/Pocket-arm64" "$build/Pocket-x86_64" -output "$bundle/Contents/MacOS/Pocket"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" ../icons/pocket-512.png --out "$build/Pocket.iconset/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" ../icons/pocket-512.png --out "$build/Pocket.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
/usr/bin/python3 make_icon.py "$build/Pocket.iconset" "$bundle/Contents/Resources/Pocket.icns"
# Ad-hoc signing is for local builds only, not public distribution.
codesign --force --sign - "$bundle"
codesign --verify --strict "$bundle"
xcrun lipo -info "$bundle/Contents/MacOS/Pocket"
echo "Local build: $bundle"
