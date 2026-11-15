---
name: run-chromium-cdp
description: Run, control, and screenshot a headless Chromium over CDP. Use when asked to start or restart the test browser, open a URL, click or type on a page, take a screenshot, read console output, or verify Chrome DevTools Protocol automation.
---

# chromium-cdp

A zero-dependency Chrome DevTools Protocol controller. `browserctl.py` (Python standard library only) hand-rolls a WebSocket client and talks to a headless Chromium started on `127.0.0.1:9222`. This plugin bundles the driver, a launcher, and a test page, so it works on a clean machine with Chromium and Python 3 installed.

**Where things are:** the base directory printed when this skill loads is the skill directory. The driver lives in `<skill-dir>/driver/`; the harness is `<skill-dir>/smoke.sh`. Set `CHROMIUM_CDP_DIR` to point the harness at a different checkout of the same driver instead.

## Prerequisites

```bash
chromium --version   # any recent Chromium/Chrome; tested with 157.0.8103.38
python3 --version    # 3.8+
curl --version       # used only by the harness
```

Linux needs `--no-sandbox` when running as root in a container; the bundled launcher already passes it. On a desktop with a working display you can still run headless.

## Build

None.

## Test

There is no test suite. `smoke.sh` is the end-to-end check.

## Run (agent path)

One end-to-end run — starts a fresh browser, clicks, types, echoes, asserts, screenshots:

```bash
"<skill-dir>/smoke.sh"
# -> result: {"count":"1","log":"echo: smoke test"}
# -> OK: counter=1, echo log correct
# -> screenshot: <driver-dir>/smoke-shot.png
```

It restarts the browser first (the launcher kills a previous instance using the same profile), so it is safe to run repeatedly.

Manual driving, once a browser is running — `cd` into the driver directory first:

```bash
cd "<skill-dir>/driver"       # or: cd "$CHROMIUM_CDP_DIR"
```

| command | what it does |
|---|---|
| `python3 browserctl.py state` | title, URL, viewport, first 600 chars of body text (active tab) |
| `python3 browserctl.py goto <url>` | navigate the current tab |
| `python3 browserctl.py click <selector>` | real mouse event at the element's center |
| `python3 browserctl.py type <selector> <text>` | focus + `Input.insertText` |
| `python3 browserctl.py shot [path]` | PNG screenshot (default `<driver-dir>/shot.png`) |
| `python3 browserctl.py console [secs]` | print console output seen during the next N seconds |
| `python3 browserctl.py eval <js>` | evaluate JS, print the returned value |
| `python3 browserctl.py tabs` | list page tabs; `*` marks the one commands drive |
| `python3 browserctl.py tab <target>` | switch the active tab (persists across invocations) |
| `python3 browserctl.py newtab [url]` | open a tab and switch to it |
| `python3 browserctl.py closetab [target]` | close a tab (default: the active one) |
| `python3 browserctl.py --tab <target> <cmd>` | run one command against another tab |

### Tabs

A `<target>` is any one of:

- an index from `tabs` — `python3 browserctl.py tab 2`
- an id prefix of 4+ chars — `python3 browserctl.py tab 8A5CD587`
- a substring of the title or URL — `python3 browserctl.py tab 上海`

Commands without `--tab` drive, in order: the tab selected by `tab`, `$CDP_TAB`, then the first page tab. The selection is stored per endpoint in `${XDG_CACHE_HOME:-~/.cache}/chromium-cdp/active-tab.json`; `--tab` overrides it for a single call.

```bash
python3 browserctl.py tabs                       # what's open
python3 browserctl.py newtab https://example.com # open + switch
python3 browserctl.py --tab 北京 state             # peek at another tab
python3 browserctl.py tab 0 && python3 browserctl.py shot a.png
```

Tab order in `/json/list` changes as tabs are opened and activated, so re-run `tabs` before relying on an index; ids are stable.

Start or restart the browser manually:

```bash
cd "<skill-dir>/driver"
./start-inspect-browser.sh [file-or-url]
# -> log: ~/.chromium-inspect.log
# -> LISTEN 0 10 127.0.0.1:9222 ...
```

Screenshots land wherever you pass to `shot`; the smoke default is `<driver-dir>/smoke-shot.png`.

## Run (human path)

Same scripts — the browser is headless, so no window opens. To watch or take over from another machine, tunnel the port and use `chrome://inspect` in a local Chrome:

```bash
ssh -N -L 9222:127.0.0.1:9222 <user>@<this-host>
```

`chrome://inspect` → Configure → `localhost:9222` → inspect → `Ctrl+Shift+M` for the live screencast (JPEG frames rendered on this host, not HTML).

## Gotchas

- **Proxies break localhost.** If `http_proxy` is set, plain `curl http://127.0.0.1:9222/json/version` returns an empty body; always add `--noproxy '*'`. `browserctl.py` bypasses the proxy with `ProxyHandler({})`.
- **Commands drive one tab at a time.** The default is the tab chosen with `tab`, else `$CDP_TAB`, else the first `type=page` target. With several tabs open, say which one — `python3 browserctl.py tab <index|id|title>` or `--tab <target>` — or you may drive the wrong page. `newtab`/`closetab` change `/json/list` order, so re-run `tabs` before using an index.
- **Console history is replayed by `Runtime.enable`.** Each CLI invocation drains the replay, so `console` only reports events after it connected.
- **`type` uses `Input.insertText`** — no keydown/keypress events. Plain inputs work; keydown-driven widgets may not react.
- **`goto` waits a fixed 1 second**, not for a load event. Slow pages can be captured half-rendered.
- **No CJK fonts on a minimal host.** Chinese text renders as boxes in screenshots and the screencast because rendering happens on the Linux side.
- **Continuous animation is expensive.** Without a GPU Chromium software-renders via SwiftShader; `requestAnimationFrame` at 60fps can saturate both cores. The bundled page uses a once-per-second clock; keep test pages static.
- **`pkill -f 'user-data-dir=...'` can match the shell running it.** Use the bracket trick: `pkill -f '[u]ser-data-dir=/home/you/.chromium-inspect'`.
- **The browser does not survive the agent session** in sandboxed/background environments. Rerun `start-inspect-browser.sh`.

## Troubleshooting

- **`urllib.error.URLError: <urlopen error [Errno 111] Connection refused>`** — no browser is listening. Run `./start-inspect-browser.sh` from the driver directory.
- **`curl` to 9222 prints nothing while the browser is up** — the proxy ate it. Add `--noproxy '*'`.
- **`element not found: <selector>` (exit 1)** — wrong selector or the page hasn't loaded. Check with `python3 browserctl.py state` first.
- **`browserctl.py not found in ...`** — `CHROMIUM_CDP_DIR` points somewhere without the driver, or the plugin was installed without `driver/`. Unset it or fix the path.
