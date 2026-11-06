#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
UI="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$UI/../.." && pwd)"
OUT="${OUTPUT_DIR:-$ROOT/dist}"
APP="$OUT/Speech Runtime.app"
IDENTITY="${SIGNING_IDENTITY:--}"

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || {
  echo 'Build requires an Apple Silicon Mac, Xcode 26+ and the repository Rust toolchain.' >&2; exit 2;
}
command -v cargo >/dev/null || { echo 'cargo is required to build the existing worker.' >&2; exit 2; }
SDK="$(xcrun --sdk macosx --show-sdk-version)"
[[ "${SDK%%.*}" -ge 26 ]] || { echo 'Liquid Glass requires the macOS 26 SDK or newer for this build.' >&2; exit 2; }

# No models, audio datasets or long-running benchmarks are downloaded/run during packaging.
swift build --package-path "$ROOT/runtime/daemon" -c release
cargo build --manifest-path "$ROOT/runtime/worker/Cargo.toml" --target-dir "$ROOT/runtime/worker/target" --locked --release --bin sr-worker --example sr-model-prepare
swift build --package-path "$UI" -c release
swift test --package-path "$UI" -q
UIBIN="$(swift build --package-path "$UI" -c release --show-bin-path)"
COREBIN="$(swift build --package-path "$ROOT/runtime/daemon" -c release --show-bin-path)"

mkdir -p "$OUT"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$UIBIN/SpeechRuntimeApp" "$APP/Contents/MacOS/"
cp "$COREBIN/speech-runtime" "$COREBIN/sr" "$APP/Contents/MacOS/"
cp "$ROOT/runtime/worker/target/release/sr-worker" "$ROOT/runtime/worker/target/release/examples/sr-model-prepare" "$APP/Contents/MacOS/"
cp "$UI/Resources/Info.plist" "$APP/Contents/Info.plist"
RESOURCE="$UIBIN/SpeechRuntimeMenu_SpeechRuntimeApp.bundle"
[[ -d "$RESOURCE" ]] || { echo "Missing SwiftPM resource bundle: $RESOURCE" >&2; exit 1; }
ditto "$RESOURCE" "$APP/Contents/Resources/SpeechRuntimeMenu_SpeechRuntimeApp.bundle"
cp "$UI/SOURCES.md" "$APP/Contents/Resources/"
if [[ -f "$ROOT/LICENSE" ]]; then cp "$ROOT/LICENSE" "$APP/Contents/Resources/PROJECT_LICENSE"; fi
xcrun swift "$HERE/make-icon.swift" "$APP/Contents/Resources/AppIcon.icns"
plutil -lint "$APP/Contents/Info.plist"

sign_one() {
  if [[ "$IDENTITY" == - ]]; then codesign --force --sign - "$1"
  else codesign --force --options runtime --timestamp --sign "$IDENTITY" "$1"; fi
}
for NAME in sr-worker sr-model-prepare speech-runtime sr SpeechRuntimeApp; do
  sign_one "$APP/Contents/MacOS/$NAME"
done
sign_one "$APP"
codesign --verify --deep --strict "$APP"

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/speech-runtime-dmg.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/Speech Runtime.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname 'Speech Runtime' -srcfolder "$STAGE" -format UDZO -ov "$OUT/SpeechRuntime-macos-arm64.dmg"
shasum -a 256 "$OUT/SpeechRuntime-macos-arm64.dmg" > "$OUT/SpeechRuntime-macos-arm64.dmg.sha256"
printf '\nBuilt: %s\nDMG: %s\n' "$APP" "$OUT/SpeechRuntime-macos-arm64.dmg"
if [[ "$IDENTITY" == - ]]; then
  echo 'Local ad-hoc build. Developer ID signing/notarization is still required for public distribution.'
fi
