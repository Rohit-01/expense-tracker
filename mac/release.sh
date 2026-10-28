#!/bin/bash
# Run only when deliberately preparing an authorized public release. This script
# uploads signed artifacts to Apple's notary service; build.sh is entirely local.
set -euo pipefail
cd "$(dirname "$0")"
identity="${POCKET_SIGNING_IDENTITY:?Set the full Developer ID Application certificate name}"
profile="${POCKET_NOTARY_PROFILE:?Set a notarytool Keychain profile name}"
if [[ "$identity" != 'Developer ID Application: '* ]]; then
  echo 'A Developer ID Application certificate is required; ad-hoc builds cannot be published.' >&2; exit 1
fi
bash build.sh release
bundle="$PWD/build/release/Pocket.app"
release="$PWD/release/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$release/staging"
codesign --force --options runtime --timestamp --sign "$identity" "$bundle"
codesign --verify --strict --verbose=2 "$bundle"
ditto -c -k --keepParent "$bundle" "$release/Pocket-notarization.zip"
xcrun notarytool submit "$release/Pocket-notarization.zip" --keychain-profile "$profile" --wait --output-format json > "$release/app-notarization.json"
/usr/bin/python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))["status"] == "Accepted" else "Apple did not accept the app; inspect the notarization report.")' "$release/app-notarization.json"
xcrun stapler staple "$bundle"
xcrun stapler validate "$bundle"
spctl --assess --type execute --verbose=2 "$bundle"
ditto "$bundle" "$release/staging/Pocket.app"
ln -s /Applications "$release/staging/Applications"
hdiutil create -volname Pocket -srcfolder "$release/staging" -ov -format UDZO "$release/Pocket.dmg"
codesign --timestamp --sign "$identity" "$release/Pocket.dmg"
xcrun notarytool submit "$release/Pocket.dmg" --keychain-profile "$profile" --wait --output-format json > "$release/dmg-notarization.json"
/usr/bin/python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))["status"] == "Accepted" else "Apple did not accept the DMG; inspect the notarization report.")' "$release/dmg-notarization.json"
xcrun stapler staple "$release/Pocket.dmg"
xcrun stapler validate "$release/Pocket.dmg"
shasum -a 256 "$release/Pocket.dmg" > "$release/Pocket.dmg.sha256"
echo "Verified release prepared at $release/Pocket.dmg. No download has been published."
