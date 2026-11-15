#!/usr/bin/env python3
"""Zero-dependency browser control over CDP (Chrome DevTools Protocol).

Requires a Chromium started with --remote-debugging-port=9222.

Usage:
  browserctl.py state
  browserctl.py goto <url>
  browserctl.py click <selector>
  browserctl.py type <selector> <text>
  browserctl.py shot [path]
  browserctl.py console [seconds]
  browserctl.py eval <js-expression>
"""
import base64, json, os, socket, struct, sys, time, urllib.request

ENDPOINT = os.environ.get('CDP_ENDPOINT', 'http://127.0.0.1:9222')
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def targets():
    with _opener.open(ENDPOINT + '/json/list', timeout=5) as r:
        return json.load(r)


def page_target():
    for t in targets():
        if t.get('type') == 'page':
            return t
    raise SystemExit('no page target found')


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


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    page = page_target()
    cdp = CDP(page['webSocketDebuggerUrl'])
    cdp.call('Runtime.enable', timeout=5)
    cdp.drain(0.25)  # swallow replayed console history

    cmd, *rest = argv
    if cmd == 'state':
        print(json.dumps(json.loads(cdp.state()), ensure_ascii=False, indent=2))
    elif cmd == 'goto':
        cdp.goto(rest[0])
        print('url now:', cdp.eval('location.href'))
    elif cmd == 'click':
        before = cdp.eval("document.querySelector(%s)?.textContent" % json.dumps(rest[0]))
        cdp.click(rest[0])
        print('clicked:', rest[0], '| text before:', before)
    elif cmd == 'type':
        cdp.type_text(rest[0], rest[1])
        print('typed into:', rest[0], '| value now:', cdp.eval(
            "document.querySelector(%s)?.value" % json.dumps(rest[0])))
    elif cmd == 'shot':
        p = rest[0] if rest else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'shot.png')
        print('saved:', cdp.screenshot(p))
    elif cmd == 'console':
        secs = float(rest[0]) if rest else 1.0
        cdp.drain(secs)
        for ts, typ, args in cdp.console:
            print(f'[{typ}]', *args)
    elif cmd == 'eval':
        print(cdp.eval(rest[0]))
    else:
        print('unknown command:', cmd)
        return 1
    cdp.sock.close()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
