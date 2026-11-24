"""Loopback-only operator UI for fictional demo nodes, not a LAN service."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

from .store import ExchangeError, MAX_BYTES
from .workflow import WorkflowError
from .ui import esc, render

VIEWS = {'map', 'reports', 'requests', 'commitments', 'dispatch', 'exchange', 'receipts', 'history'}
ASSETS = {'/assets/location-picker.js': ('location-picker.js','text/javascript'),
          '/assets/maps/road-detail.json': ('maps/road-detail.json','application/json'),
          '/assets/app.css': ('operator.css', 'text/css; charset=utf-8'),
          '/assets/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/assets/satellite.js': ('satellite.js', 'text/javascript; charset=utf-8'),
          '/assets/leaflet/leaflet.js': ('vendor/leaflet/leaflet.js', 'text/javascript; charset=utf-8'),
          '/assets/leaflet/leaflet.css': ('vendor/leaflet/leaflet.css', 'text/css; charset=utf-8'),
          '/assets/maps/vadodara-sentinel.jpg': ('maps/vadodara-sentinel.jpg', 'image/jpeg'),
          '/assets/maps/satellite.json': ('maps/satellite.json', 'application/json'),
          '/assets/maps/online.json': ('maps/online.json', 'application/json'),
          '/assets/maps/four-camps.json': ('maps/four-camps.json', 'application/json'),
          '/assets/maps/streets.json': ('maps/streets.json', 'application/json')}
for _image in ('layers.png', 'layers-2x.png', 'marker-icon.png', 'marker-icon-2x.png', 'marker-shadow.png'):
    ASSETS['/assets/leaflet/images/' + _image] = ('vendor/leaflet/images/' + _image, 'image/png')


def make_server(store, port=0, token=None):
    token = token or secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Do not log the access token from the first request URL.

        def reply(self, status, body, content_type='text/html; charset=utf-8', headers=None):
            body = body.encode('utf-8') if isinstance(body, str) else body
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            # Preserve same-origin form Origin headers; no-referrer makes Chrome send Origin:null.
            self.send_header('Referrer-Policy', 'same-origin')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'nonce-" + csrf + "'; connect-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; frame-src https://www.google.com; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def authorised(self):
            if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
                return False
            cookie = self.headers.get('Cookie', '')
            return any(part.strip() == 'sanjay_' + str(self.server.server_port) + '=' + token for part in cookie.split(';'))

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == '/' and secrets.compare_digest(query.get('token', [''])[0], token):
                self.reply(303, '', headers={'Location': '/', 'Set-Cookie': f'sanjay_{self.server.server_port}={token}; HttpOnly; SameSite=Strict; Path=/'})
                return
            if not self.authorised():
                self.reply(403, 'Open the private startup URL printed by main.py', 'text/plain; charset=utf-8')
                return
            try:
                if parsed.path == '/':
                    self.reply(200, render(store.snapshot(), csrf, query.get('message', [''])[0], query.get('view', ['map'])[0]))
                elif parsed.path in ASSETS:
                    filename, mime = ASSETS[parsed.path]
                    self.reply(200, (Path(__file__).with_name('static') / filename).read_bytes(), mime)
                elif parsed.path == '/state':
                    self.reply(200, json.dumps(store.snapshot(), indent=2), 'application/json')
                elif parsed.path == '/export':
                    self.reply(200, store.export_bundle(), 'application/json', {'Content-Disposition': f'attachment; filename="sanjay-{store.node}-updates.json"'})
                else:
                    self.reply(404, 'Not found', 'text/plain')
            except (ExchangeError, WorkflowError, ValueError, sqlite3.Error, OSError) as exc:
                self.reply(400, esc(exc))

        def do_POST(self):
            if not self.authorised():
                self.reply(403, 'Access denied', 'text/plain')
                return
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
                self.reply(403, 'Cross-origin request rejected', 'text/plain')
                return
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                if self.headers.get('Transfer-Encoding') or not 0 <= length <= MAX_BYTES:
                    raise ExchangeError('Missing/invalid body size or body exceeds 1 MB')
                self.connection.settimeout(10)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ExchangeError('Incomplete request')
                if self.path == '/import':
                    if not secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), csrf):
                        self.reply(403, 'CSRF check failed', 'text/plain'); return
                    result = store.import_bundle(raw)
                    self.reply(200, json.dumps(result), 'application/json')
                elif self.path == '/command':
                    values = parse_qs(raw.decode('utf-8'), strict_parsing=True)
                    if any(len(v) != 1 for v in values.values()) or not secrets.compare_digest(values.get('csrf', [''])[0], csrf):
                        self.reply(403, 'CSRF check failed', 'text/plain'); return
                    fields = {k: v[0] for k, v in values.items()}
                    kind = fields.pop('kind', '')
                    fields.pop('csrf', None)
                    view = fields.pop('return_view', 'requests')
                    if view not in VIEWS:
                        raise ExchangeError('Unknown return view')
                    if 'quantity' in fields:
                        fields['quantity'] = int(fields['quantity'])
                    id_key = {'request': 'request_id', 'grant': 'grant_id', 'dispatch': 'dispatch_id', 'receipt': 'receipt_id'}.get(kind)
                    if id_key:
                        fields[id_key] = str(uuid.uuid4())
                    if kind == 'dispatch':
                        grant = store.snapshot()['state']['grants'].get(fields.get('grant_id'))
                        if not grant:
                            raise ExchangeError('Unknown grant')
                        fields['quantity'] = grant['quantity']
                    store.command(kind, fields)
                    self.reply(303, '', headers={'Location': '/?' + urlencode({'view': view, 'message': 'Saved durably on this node. Transfer updates to inform the other node.'})})
                else:
                    self.reply(404, 'Not found', 'text/plain')
            except (ExchangeError, WorkflowError, ValueError, UnicodeError, sqlite3.Error, OSError) as exc:
                self.reply(400, str(exc), 'text/plain; charset=utf-8')
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server, f'http://127.0.0.1:{server.server_port}/?token={token}'
