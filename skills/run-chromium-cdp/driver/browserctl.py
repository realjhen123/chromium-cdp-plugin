#!/usr/bin/env python3
"""Zero-dependency browser control over CDP (Chrome DevTools Protocol).

Requires a Chromium started with --remote-debugging-port=9222.

Usage:
  browserctl.py [--tab <index|id|title>] state
  browserctl.py [--tab <index|id|title>] goto <url>
  browserctl.py [--tab <index|id|title>] click <selector>
  browserctl.py [--tab <index|id|title>] type <selector> <text>
  browserctl.py [--tab <index|id|title>] shot [path]
  browserctl.py [--tab <index|id|title>] console [seconds]
  browserctl.py [--tab <index|id|title>] eval <js-expression>
  browserctl.py tabs                 list page tabs, * marks the active one
  browserctl.py tab <target>         switch the active tab (persists)
  browserctl.py newtab [url]         open a tab and make it active
  browserctl.py closetab [target]    close a tab (default: the active one)

A <target> is a tab index from `tabs`, an id prefix (>= 4 chars), or a
substring of the tab's title or URL. Commands without --tab use $CDP_TAB,
then the tab selected by `tab`, then the first page tab.
"""
import base64, json, os, socket, struct, sys, time, urllib.error, urllib.request
from urllib.parse import quote

ENDPOINT = os.environ.get('CDP_ENDPOINT', 'http://127.0.0.1:9222')
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

STATE_DIR = os.path.join(
    os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache')), 'chromium-cdp')


def targets():
    with _opener.open(ENDPOINT + '/json/list', timeout=5) as r:
        return json.load(r)


def page_targets():
    return [t for t in targets() if t.get('type') == 'page']


def page_target():
    """First page tab — the default when no tab has been selected."""
    pts = page_targets()
    if not pts:
        raise SystemExit('no page target found')
    return pts[0]


# --- active-tab state ----------------------------------------------------
def _state_path():
    return os.path.join(STATE_DIR, 'active-tab.json')


def _load_state():
    try:
        with open(_state_path()) as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = _state_path() + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(state, f)
        os.replace(tmp, _state_path())
    except OSError:
        pass  # read-only home: selection just won't persist


def active_tab_id():
    return _load_state().get(ENDPOINT) or ''


def set_active_tab(tab_id):
    state = _load_state()
    if tab_id:
        state[ENDPOINT] = tab_id
    else:
        state.pop(ENDPOINT, None)
    _save_state(state)


def warn_env_override():
    """Tell the user when a saved switch will not be the tab commands drive."""
    if os.environ.get('CDP_TAB'):
        print('note: $CDP_TAB is set and takes precedence over the saved '
              'selection; unset it or pass --tab', file=sys.stderr)


def find_tab(spec, tabs=None):
    """Resolve index / id prefix / title-or-URL substring to a tab."""
    tabs = page_targets() if tabs is None else tabs
    if not tabs:
        raise SystemExit('no page target found')
    if spec is None or spec == '':
        return tabs[0]
    s = str(spec)
    index = int(s) if s.isdigit() else None
    if index is not None and index < len(tabs):
        return tabs[index]
    # An id prefix can be all digits too (Chrome ids are hex), and users type
    # ids in lowercase, so match ids case-insensitively before giving up.
    # Prefixes shorter than 4 chars are never ids: `tab 5` with 3 tabs is an
    # out-of-range index, not a one-digit prefix that happens to match.
    by_id = []
    if len(s) >= 4:
        by_id = [t for t in tabs if t['id'].lower().startswith(s.lower())]
    if len(by_id) > 1:
        raise SystemExit(f'tab id prefix {s!r} is ambiguous ({len(by_id)} matches)')
    if by_id:
        return by_id[0]
    by_text = [t for t in tabs
               if s in (t.get('title') or '') or s in (t.get('url') or '')]
    if len(by_text) > 1:
        raise SystemExit(f'tab {s!r} is ambiguous ({len(by_text)} matches)')
    if by_text:
        return by_text[0]
    if index is not None:
        raise SystemExit(f'tab index {index} out of range (0..{len(tabs) - 1})')
    raise SystemExit(f'no tab matching {s!r} (see `browserctl.py tabs`)')


def selected_tab(tabs=None):
    """The tab the persisted selection points at, or None if unset/stale."""
    tabs = page_targets() if tabs is None else tabs
    saved = active_tab_id()
    for t in tabs:
        if t['id'] == saved:
            return t
    return None


def resolve_target(spec=None):
    """Pick the tab a normal command should drive.

    Explicit overrides beat the saved selection: --tab, then $CDP_TAB, then
    the tab chosen by `tab`, then the first page tab.
    """
    tabs = page_targets()
    if not tabs:
        raise SystemExit('no page target found')
    if spec:
        return find_tab(spec, tabs)
    env = os.environ.get('CDP_TAB') or ''
    if env:
        return find_tab(env, tabs)
    chosen = selected_tab(tabs)
    if chosen:
        return chosen
    if active_tab_id():
        set_active_tab('')  # the browser restarted or the tab was closed
        if len(tabs) > 1:
            print(f'warning: the selected tab is gone; using the first of {len(tabs)} '
                  'tabs — pick one with `browserctl.py tabs` / `tab`', file=sys.stderr)
    return tabs[0]


class CDP:
    def __init__(self, url):
        assert url.startswith('ws://'), url
        rest = url[len('ws://'):]
        hostport, path = rest.split('/', 1)
        host, port = hostport.rsplit(':', 1)
        self.sock = socket.create_connection((host, int(port)), timeout=10)
        self.sock.settimeout(0.5)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError('ws handshake closed')
            buf += chunk
        head, _, self.buf = buf.partition(b'\r\n\r\n')
        if b' 101 ' not in head.split(b'\r\n')[0]:
            raise RuntimeError('ws handshake failed: ' + head.decode(errors='replace')[:200])
        self.msg_id = 0
        self.console = []

    # --- wire ------------------------------------------------------------
    def _send_text(self, text):
        data = text.encode()
        mask = os.urandom(4)
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126); hdr += struct.pack('>H', n)
        else:
            hdr.append(0x80 | 127); hdr += struct.pack('>Q', n)
        hdr += mask
        self.sock.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError('ws closed')
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _recv_message(self):
        """Returns str payload or None on timeout. Handles fragmentation."""
        try:
            h = self._recv_exact(2)
        except (TimeoutError, socket.timeout):
            return None
        opcode = h[0] & 0x0F
        fin = h[0] & 0x80
        ln = h[1] & 0x7F
        if ln == 126:
            ln = struct.unpack('>H', self._recv_exact(2))[0]
        elif ln == 127:
            ln = struct.unpack('>Q', self._recv_exact(8))[0]
        payload = self._recv_exact(ln)
        if opcode == 0x9:
            mask = os.urandom(4)
            self.sock.sendall(bytes([0x8A, 0x80]) + mask +
                              bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
            return self._recv_message()
        if opcode in (0x1, 0x2, 0x0):
            data = payload
            while not fin:
                h2 = self._recv_exact(2)
                fin = h2[0] & 0x80
                ln2 = h2[1] & 0x7F
                if ln2 == 126:
                    ln2 = struct.unpack('>H', self._recv_exact(2))[0]
                elif ln2 == 127:
                    ln2 = struct.unpack('>Q', self._recv_exact(8))[0]
                data += self._recv_exact(ln2)
            return data.decode(errors='replace')
        return ''

    def _pump(self, deadline):
        """Read messages until deadline; record console events."""
        while time.time() < deadline:
            m = self._recv_message()
            if m is None:
                continue
            try:
                obj = json.loads(m)
            except json.JSONDecodeError:
                continue
            if obj.get('method') == 'Runtime.consoleAPICalled':
                args = [a.get('value', a.get('description', ''))
                        for a in obj['params'].get('args', [])]
                self.console.append((time.time(), obj['params'].get('type'), args))
            yield obj

    def call(self, method, params=None, timeout=15):
        self.msg_id += 1
        mid = self.msg_id
        self._send_text(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
        deadline = time.time() + timeout
        for obj in self._pump(deadline):
            if obj.get('id') == mid:
                if 'error' in obj:
                    raise RuntimeError(f"{method}: {obj['error']}")
                return obj.get('result', {})
        raise TimeoutError(method)

    def drain(self, seconds=0.3):
        for _ in self._pump(time.time() + seconds):
            pass

    # --- helpers ---------------------------------------------------------
    def eval(self, expression):
        r = self.call('Runtime.evaluate', {
            'expression': expression, 'returnByValue': True, 'awaitPromise': True,
        })
        if 'exceptionDetails' in r:
            raise RuntimeError(json.dumps(r['exceptionDetails'])[:400])
        return r['result'].get('value')

    def center(self, selector):
        js = """(() => { const e = document.querySelector(%s);
            if (!e) return null; e.scrollIntoView({block:'center'});
            const r = e.getBoundingClientRect();
            return {x: r.left + r.width/2, y: r.top + r.height/2,
                    w: r.width, h: r.height}; })()""" % json.dumps(selector)
        return self.eval(js)

    def click(self, selector):
        c = self.center(selector)
        if not c:
            raise SystemExit(f'element not found: {selector}')
        for typ, btn, clicks in (('mouseMoved', 'none', 0),
                                 ('mousePressed', 'left', 1),
                                 ('mouseReleased', 'left', 1)):
            self.call('Input.dispatchMouseEvent', {
                'type': typ, 'x': c['x'], 'y': c['y'],
                'button': btn, 'buttons': 1 if typ != 'mouseMoved' else 0,
                'clickCount': clicks,
            })
        self.drain(0.2)

    def type_text(self, selector, text):
        self.eval(f"document.querySelector({json.dumps(selector)}).focus()")
        self.call('Input.insertText', {'text': text})
        self.drain(0.2)

    def goto(self, url):
        self.call('Page.enable', timeout=5)
        self.call('Page.navigate', {'url': url})
        time.sleep(1.0)
        self.drain(0.3)

    def screenshot(self, path):
        data = self.call('Page.captureScreenshot', {'format': 'png'}, timeout=30)['data']
        with open(path, 'wb') as f:
            f.write(base64.b64decode(data))
        return path

    def state(self):
        return self.eval("""JSON.stringify({
            title: document.title, url: location.href,
            viewport: innerWidth + 'x' + innerHeight,
            text: (document.body.innerText || '').slice(0, 600)
        })""")


# --- tab management ------------------------------------------------------
def browser_ws_url():
    with _opener.open(ENDPOINT + '/json/version', timeout=5) as r:
        return json.load(r)['webSocketDebuggerUrl']


def activate_tab(tab_id):
    """Best-effort: bring the tab to the front of a headed browser."""
    try:
        cdp = CDP(browser_ws_url())
        try:
            cdp.call('Target.activateTarget', {'targetId': tab_id}, timeout=5)
        finally:
            cdp.sock.close()
    except Exception as e:
        print(f'warning: could not activate tab in the UI: {e}', file=sys.stderr)


def open_tab(url='about:blank'):
    target = ENDPOINT + '/json/new?' + quote(url, safe='')
    try:
        with _opener.open(urllib.request.Request(target, method='PUT'), timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code not in (404, 405, 501):
            raise
        with _opener.open(target, timeout=10) as r:  # older Chrome takes GET
            return json.load(r)


def close_tab(tab):
    with _opener.open(ENDPOINT + '/json/close/' + tab['id'], timeout=10) as r:
        return r.read().decode(errors='replace').strip()


def print_tabs():
    tabs = page_targets()
    if not tabs:
        print('no page tabs')
        return
    # Star the tab a command would actually drive — same order as
    # resolve_target — so `*` cannot point at a tab the next command ignores.
    try:
        current = resolve_target()['id']
    except SystemExit as e:
        print(f'warning: {e}', file=sys.stderr)
        current = ''
    for i, t in enumerate(tabs):
        print(f"{'*' if t['id'] == current else ' '} [{i}] {t['id'][:8]}  {t.get('title') or '(untitled)'}")
        print(f"       {t.get('url') or ''}")


def main(argv):
    if not argv:
        print(__doc__)
        return 1

    spec = None
    words = []
    i = 0
    while i < len(argv):
        if argv[i] in ('--tab', '-t'):
            if i + 1 >= len(argv):
                print('--tab needs a tab index, id, or title', file=sys.stderr)
                return 2
            spec = argv[i + 1]
            i += 2
            continue
        words.append(argv[i])
        i += 1
    if not words:
        print(__doc__)
        return 1

    cmd, *args = words

    if cmd == 'tabs':
        print_tabs()
        return 0
    if cmd in ('tab', 'switch'):
        if not args:
            print('usage: browserctl.py tab <index|id|title>', file=sys.stderr)
            return 2
        tab = find_tab(args[0])
        set_active_tab(tab['id'])
        activate_tab(tab['id'])
        warn_env_override()
        print('active tab:', tab['id'][:8], '|', tab.get('title'), '|', tab.get('url'))
        return 0
    if cmd == 'newtab':
        tab = open_tab(args[0] if args else 'about:blank')
        set_active_tab(tab['id'])
        warn_env_override()
        print('opened tab:', tab['id'][:8], '|', tab.get('url'))
        return 0
    if cmd == 'closetab':
        tab = resolve_target(args[0] if args else spec)
        close_tab(tab)
        print('closed tab:', tab['id'][:8], '|', tab.get('title'))
        time.sleep(0.2)
        if active_tab_id() == tab['id']:
            remaining = [t for t in page_targets() if t['id'] != tab['id']]
            set_active_tab(remaining[0]['id'] if remaining else '')
        return 0

    page = resolve_target(spec)
    cdp = CDP(page['webSocketDebuggerUrl'])
    cdp.call('Runtime.enable', timeout=5)
    cdp.drain(0.25)  # swallow replayed console history

    if cmd == 'state':
        print(json.dumps(json.loads(cdp.state()), ensure_ascii=False, indent=2))
    elif cmd == 'goto':
        cdp.goto(args[0])
        print('url now:', cdp.eval('location.href'))
    elif cmd == 'click':
        before = cdp.eval("document.querySelector(%s)?.textContent" % json.dumps(args[0]))
        cdp.click(args[0])
        print('clicked:', args[0], '| text before:', before)
    elif cmd == 'type':
        cdp.type_text(args[0], args[1])
        print('typed into:', args[0], '| value now:', cdp.eval(
            "document.querySelector(%s)?.value" % json.dumps(args[0])))
    elif cmd == 'shot':
        p = args[0] if args else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'shot.png')
        print('saved:', cdp.screenshot(p))
    elif cmd == 'console':
        secs = float(args[0]) if args else 1.0
        cdp.drain(secs)
        for ts, typ, values in cdp.console:
            print(f'[{typ}]', *values)
    elif cmd == 'eval':
        print(cdp.eval(args[0]))
    else:
        print('unknown command:', cmd)
        return 1
    cdp.sock.close()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
