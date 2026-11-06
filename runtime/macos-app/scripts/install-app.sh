#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
SOURCE="${1:-$ROOT/dist/Speech Runtime.app}"
DEST="$HOME/Applications/Speech Runtime.app"
[[ -d "$SOURCE" ]] || { echo 'Build the app first.' >&2; exit 2; }
if launchctl print "gui/$(id -u)/com.speechruntime.agent" >/dev/null 2>&1; then
  echo 'Turn the service off and quit its menu interface before replacing the app.' >&2; exit 2
fi
if pgrep -x SpeechRuntimeApp >/dev/null 2>&1; then
  echo 'Quit the menu interface before replacing the app.' >&2; exit 2
fi
[[ ! -e "$DEST.previous" ]] || { echo 'A previous installation backup already exists; move it aside first.' >&2; exit 2; }
mkdir -p "$HOME/Applications"
if [[ -d "$DEST" ]]; then mv "$DEST" "$DEST.previous"; fi
if ! ditto "$SOURCE" "$DEST"; then
  rm -rf "$DEST"
  if [[ -d "$DEST.previous" ]]; then mv "$DEST.previous" "$DEST"; fi
  exit 1
fi
rm -rf "$DEST.previous"
open "$DEST"
