#!/usr/bin/env bash
# Bundled smoke test for the chromium-cdp plugin.
# Usage: smoke.sh [url] [screenshot-path]
# Driver: $CHROMIUM_CDP_DIR if set, else the driver/ directory beside this file.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT="${CHROMIUM_CDP_DIR:-$SKILL_DIR/driver}"
[ -f "$UNIT/browserctl.py" ] || { echo "ERROR: browserctl.py not found in $UNIT" >&2; exit 1; }
cd "$UNIT"
unset CDP_TAB  # the harness asserts the tab it drives, not the caller's

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

# Tabs: newtab switches to the new tab; closetab drops it and falls back.
BEFORE="$(python3 browserctl.py eval 'document.title')"
ORIG_ID="$(python3 browserctl.py tabs | sed -n 's/^\* \[[0-9]*\] \([0-9A-F]*\).*/\1/p')"
[ -n "$ORIG_ID" ] || { echo 'ERROR: no tab starred before newtab' >&2; exit 1; }
python3 browserctl.py newtab "file://$UNIT/inspect-test.html" >/dev/null
TABS="$(python3 browserctl.py tabs | grep -c '^[ *] \[')" || true
[ "$TABS" = "2" ] || { echo "ERROR: expected 2 tabs after newtab, got $TABS" >&2; exit 1; }

# $CDP_TAB outranks the saved selection, and `tabs` stars what commands drive.
ENV_TITLE="$(CDP_TAB="$ORIG_ID" python3 browserctl.py eval 'document.title')"
[ "$ENV_TITLE" = "$BEFORE" ] \
  || { echo "ERROR: CDP_TAB did not override the selection (got '$ENV_TITLE')" >&2; exit 1; }
ENV_STAR="$(CDP_TAB="$ORIG_ID" python3 browserctl.py tabs | sed -n 's/^\* \[[0-9]*\] \([0-9A-F]*\).*/\1/p')"
[ "$ENV_STAR" = "$ORIG_ID" ] \
  || { echo "ERROR: tabs starred '$ENV_STAR' not the CDP_TAB-driven $ORIG_ID" >&2; exit 1; }

# An out-of-range number is an index error, not a one-char id prefix.
if python3 browserctl.py --tab 5 eval 'document.title' >/dev/null 2>&1; then
  echo 'ERROR: out-of-range numeric tab spec was accepted' >&2; exit 1
fi

# Pick the active tab by a lowercase id prefix; it must resolve to the same tab.
NEW_ID="$(python3 browserctl.py tabs | sed -n 's/^\* \[[0-9]*\] \([0-9A-F]*\).*/\1/p')"
LOWER_ID="$(printf %s "$NEW_ID" | tr 'A-F' 'a-f')"
python3 browserctl.py tab "$LOWER_ID" >/dev/null
ACTIVE_ID="$(python3 browserctl.py tabs | sed -n 's/^\* \[[0-9]*\] \([0-9A-F]*\).*/\1/p')"
[ "$ACTIVE_ID" = "$NEW_ID" ] \
  || { echo "ERROR: id prefix '$LOWER_ID' did not resolve to $NEW_ID (got '$ACTIVE_ID')" >&2; exit 1; }

python3 browserctl.py closetab >/dev/null
TABS="$(python3 browserctl.py tabs | grep -c '^[ *] \[')" || true
[ "$TABS" = "1" ] || { echo "ERROR: expected 1 tab after closetab, got $TABS" >&2; exit 1; }
[ "$(python3 browserctl.py eval 'document.title')" = "$BEFORE" ] \
  || { echo 'ERROR: closetab did not fall back to the original tab' >&2; exit 1; }
echo "OK: tabs — newtab switched, closetab restored '$BEFORE'"
