#!/usr/bin/env bash
# Bundled smoke test for the chromium-cdp plugin.
# Usage: smoke.sh [url] [screenshot-path]
# Driver: $CHROMIUM_CDP_DIR if set, else the driver/ directory beside this file.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT="${CHROMIUM_CDP_DIR:-$SKILL_DIR/driver}"
[ -f "$UNIT/browserctl.py" ] || { echo "ERROR: browserctl.py not found in $UNIT" >&2; exit 1; }
cd "$UNIT"

URL="${1:-file://$UNIT/inspect-test.html}"
SHOT="${2:-$UNIT/smoke-shot.png}"

./start-inspect-browser.sh "$URL"

# Wait for the debugging endpoint to answer.
for _ in $(seq 1 40); do
  curl -sf --noproxy '*' --max-time 1 http://127.0.0.1:9222/json/version >/dev/null 2>&1 && break
  sleep 0.25
done
curl -sf --noproxy '*' --max-time 2 http://127.0.0.1:9222/json/version >/dev/null \
  || { echo 'ERROR: 127.0.0.1:9222 is not reachable' >&2; exit 1; }

# Wait until the page's test controls exist.
ready=False
for _ in $(seq 1 40); do
  ready="$(python3 browserctl.py eval '!!document.querySelector("#btn")' 2>/dev/null || true)"
  [ "$ready" = "True" ] && break
  sleep 0.25
done
[ "$ready" = "True" ] || { echo 'ERROR: test page did not load (#btn missing)' >&2; exit 1; }

# Drive one real user flow: click, type, echo.
python3 browserctl.py click '#btn'
python3 browserctl.py type '#text' 'smoke test'
python3 browserctl.py click '#echo'

RESULT="$(python3 browserctl.py eval "JSON.stringify({count: document.querySelector('#count').textContent, log: document.querySelector('#log').textContent})")"
echo "result: $RESULT"

python3 - "$RESULT" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
assert d["count"] == "1", d
assert d["log"] == "echo: smoke test", d
print("OK: counter=1, echo log correct")
PY

python3 browserctl.py shot "$SHOT"
[ -s "$SHOT" ] || { echo "ERROR: screenshot missing: $SHOT" >&2; exit 1; }
echo "screenshot: $SHOT"
