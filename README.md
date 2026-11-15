# chromium-cdp

A Claude Code plugin that drives a headless Chromium over the Chrome DevTools Protocol. It bundles:

- `browserctl.py` — a zero-dependency CDP client (Python standard library only)
- `start-inspect-browser.sh` — launches headless Chromium with `--remote-debugging-port=9222`
- `inspect-test.html` — a test page with a counter, an input/Echo pair, and a clock
- the `run-chromium-cdp` skill — launch, click, type, screenshot, read console, run JS

No npm, no Playwright, no MCP server. Python 3 and Chromium are the only requirements.

## Install

Clone the repository, then register the checkout as a marketplace (this CLI version accepts local paths, `owner/repo`, or `https://` URLs — not `ssh://`):

```bash
git clone aigit@git.home:chromium-cdp-plugin.git
```

From Claude Code:

```
/plugin marketplace add ./chromium-cdp-plugin
/plugin install chromium-cdp@aigit-plugins
```

Or from a shell:

```bash
claude plugin marketplace add ./chromium-cdp-plugin
claude plugin install chromium-cdp@aigit-plugins
```

If `git.home` later gets an HTTPS front end, its URL can be passed to `marketplace add` directly.

For local development, load the checkout directly:

```bash
claude --plugin-dir /path/to/chromium-cdp-plugin
```

## Use

The skill loads automatically when a request matches, or invoke it explicitly with `/chromium-cdp:run-chromium-cdp`.

Smoke-test the whole chain (starts a browser, clicks, types, echoes, asserts, screenshots):

```bash
"<plugin-root>/skills/run-chromium-cdp/smoke.sh"
```

Manual control:

```bash
cd "<plugin-root>/skills/run-chromium-cdp/driver"
python3 browserctl.py state
python3 browserctl.py goto https://example.com
python3 browserctl.py click '#btn'
python3 browserctl.py type '#text' 'hello'
python3 browserctl.py shot /tmp/proof.png
python3 browserctl.py console 2
python3 browserctl.py eval 'document.title'
```

## Watching from another machine

The browser is headless. Tunnel the debugging port and point a local Chrome at it:

```bash
ssh -N -L 9222:127.0.0.1:9222 <user>@<host>
```

Then open `chrome://inspect` → Configure → `localhost:9222` → inspect → `Ctrl+Shift+M` for the live screencast.

Note: what you see is the **rendered pixel stream** from the host (JPEG frames), not HTML. Fonts, canvas, and layout come from the host's Chromium.

## Configuration

Set `CHROMIUM_CDP_DIR` to drive a different checkout of the same driver instead of the bundled copy:

```bash
export CHROMIUM_CDP_DIR=/path/to/chromium-cdp
```

## Uninstall

```
/plugin uninstall chromium-cdp
/plugin marketplace remove aigit-plugins
```
