#!/usr/bin/env bash
# Non-destructive smoke check for the Parakeet-only local API.
set -euo pipefail
url="${SPEECH_RUNTIME_URL:-http://127.0.0.1:8477}"
token_file="${HOME}/Library/Application Support/SpeechRuntime/token"
if [[ ! -r "$token_file" ]]; then
  echo "Speech Runtime token missing: $token_file" >&2
  exit 1
fi
token=$(<"$token_file")
[[ -n "$token" ]] || { echo 'Speech Runtime token is empty' >&2; exit 1; }
curl -fsS --max-time 5 "$url/health" >/dev/null
curl -fsS --max-time 10 -H "Authorization: Bearer $token" \
    "$url/v1/models" | python3 -c '
import json, sys
models = json.load(sys.stdin).get("models", [])
parakeet = [m for m in models if m.get("model") == "parakeet-ultra"]
if len(parakeet) != 1:
    sys.exit("Parakeet Ultra is not declared by the running Speech Runtime")
model = parakeet[0]
print("Speech Runtime reachable; model=parakeet-ultra; loaded=" + str(model.get("loaded")))
print("Capabilities:", json.dumps(model.get("capabilities", {}), sort_keys=True))
'
