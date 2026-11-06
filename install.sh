#!/bin/bash
# Speech Runtime installer: downloads the latest release DMG from GitHub,
# verifies its SHA-256, and copies the app to ~/Applications (no sudo).
# Idempotent: re-running replaces an existing installation.
set -euo pipefail

REPO="1tuz/Speech-Runtime"
APP_NAME="Speech Runtime.app"
DEST_DIR="$HOME/Applications"
DEST_APP="$DEST_DIR/$APP_NAME"
TMP_DIR="$(mktemp -d)"
MOUNT_POINT=""
DMG_PATH=""
SHA_PATH=""

cleanup() {
    [ -n "$MOUNT_POINT" ] && hdiutil detach "$MOUNT_POINT" -quiet >/dev/null 2>&1 || true
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

die() { echo "install.sh: $1" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || die "curl is required but not found."

echo "Fetching the latest release from GitHub..."
API_JSON="$TMP_DIR/release.json"
if ! curl --fail --silent --show-error --location \
     "https://api.github.com/repos/${REPO}/releases/latest" -o "$API_JSON"; then
    die "Could not reach the GitHub API. Check your network access to api.github.com and retry."
fi

# Asset URLs are read straight from the downloaded JSON payload.
ASSETS="$(cat "$API_JSON")"
DMG_URL="$(printf '%s' "$ASSETS" | grep -o '"browser_download_url": *"[^"]*\.dmg"' | head -n1 | sed 's/.*"\(https[^"]*\)"/\1/')"
SHA_URL="$(printf '%s' "$ASSETS" | grep -o '"browser_download_url": *"[^"]*\.sha256"' | head -n1 | sed 's/.*"\(https[^"]*\)"/\1/')"
[ -n "$DMG_URL" ] || die "The latest release has no .dmg asset."
[ -n "$SHA_URL" ] || die "The latest release has no .sha256 asset."

DMG_NAME="${DMG_URL##*/}"
DMG_PATH="$TMP_DIR/$DMG_NAME"
SHA_PATH="$TMP_DIR/$DMG_NAME.sha256"

echo "Downloading $DMG_NAME..."
if ! curl --fail --silent --show-error --location "$DMG_URL" -o "$DMG_PATH"; then
    die "Download failed. No file was installed."
fi

echo "Verifying SHA-256 checksum..."
if ! curl --fail --silent --show-error --location "$SHA_URL" -o "$SHA_PATH"; then
    die "Could not download the checksum file. The DMG was not installed."
fi

EXPECTED="$(awk '{print tolower($1)}' "$SHA_PATH")"
[ -n "$EXPECTED" ] || die "The checksum file is empty."
ACTUAL="$(shasum -a 256 "$DMG_PATH" | awk '{print tolower($1)}')"
if [ "$EXPECTED" != "$ACTUAL" ]; then
    rm -f "$DMG_PATH"
    die "SHA-256 mismatch: expected $EXPECTED, got $ACTUAL. The download was discarded."
fi
echo "Checksum OK."

echo "Mounting the disk image..."
MOUNT_POINT="$(hdiutil attach "$DMG_PATH" -nobrowse -readonly | tail -n1 | awk '{ for (i = NF; i > 0; i--) if ($i ~ /^\//) { print $i; exit } }')"
[ -n "$MOUNT_POINT" ] || die "Could not mount the DMG."
[ -d "$MOUNT_POINT/$APP_NAME" ] || die "The DMG does not contain '$APP_NAME'."

mkdir -p "$DEST_DIR"
# Replace an existing installation; application data lives outside the bundle.
if [ -d "$DEST_APP" ]; then
    echo "Replacing the existing installation at $DEST_APP..."
    rm -rf "$DEST_APP"
fi
if ! cp -R "$MOUNT_POINT/$APP_NAME" "$DEST_DIR/"; then
    die "Could not copy the app to $DEST_DIR. No sudo prompt is used; use /Applications manually if needed."
fi
hdiutil detach "$MOUNT_POINT" -quiet || true
MOUNT_POINT=""

echo
echo "Installed: $DEST_APP"
echo
echo "To open the application:"
echo "  open '$DEST_APP'"
echo
echo "To add the sr CLI to your PATH (for terminals and agents):"
echo "  echo 'export PATH=\"\$HOME/Applications/Speech Runtime.app/Contents/MacOS:\$PATH\"' >> ~/.zshrc"
echo
echo "For scripts and agents, the local API uses:"
echo "  SPEECH_RUNTIME_URL=http://127.0.0.1:8477"
echo "  SPEECH_RUNTIME_TOKEN=\$HOME/Library/Application Support/SpeechRuntime/token"
echo "  CLI: \$HOME/Applications/Speech Runtime.app/Contents/MacOS/sr"
echo
echo "This script does not launch the app and does not ask for sudo."
