"""First-run camp selection on loopback; no private identity is imported or cloned."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import secrets
import threading
from urllib.parse import parse_qs
import webbrowser

from .identities import CAMP_NODES

CODES={f'VAD-TEST-{node.upper()}':node for node in CAMP_NODES}

def choose_camp(port,open_browser=True):
    ready=threading.Event();selected=[];csrf=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def respond(self,status,body):
            data=body.encode();self.send_response(status)
            for k,v in {'Content-Type':'text/html; charset=utf-8','Content-Length':str(len(data)),'Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"}.items():self.send_header(k,v)
            self.end_headers();self.wfile.write(data)
        def valid_host(self):return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
        def do_GET(self):
            if not self.valid_host() or self.headers.get('Sec-Fetch-Site')=='cross-site':self.respond(403,'Local setup only');return
            options=''.join(f'<option value="{code}">{code}</option>' for code in CODES)
            self.respond(200,'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>SANJAY · Camp setup</title><style>body{font:17px system-ui;background:#111820;color:#e7eff7;max-width:600px;margin:8vh auto;padding:24px}select,button{font:inherit;padding:12px;margin:12px 0;display:block}</style><h1>Set up this camp computer</h1><p>Choose its camp setup code once. A new independent signing identity will be created on this computer. No private keys are copied.</p><p>These four bundled profiles are provisional Vadodara TEST camps, not surveyed or authorised shelters. Edit the recorded location and both boundaries after setup.</p><form method="post"><input type="hidden" name="csrf" value="'+csrf+'"><label>Camp setup code<select name="code">'+options+'</select></label><button>Set up this camp</button></form>')
        def do_POST(self):
            if not self.valid_host() or self.headers.get('Origin') not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):
                self.respond(403,'Local origin required');return
            lengths=self.headers.get_all('Content-Length',[])
            if len(lengths)!=1 or not lengths[0].isdigit() or int(lengths[0])>1024 or self.headers.get('Transfer-Encoding'):
                self.respond(400,'Invalid setup request');return
            fields=parse_qs(self.rfile.read(int(lengths[0])).decode())
            if set(fields)!={'csrf','code'} or any(len(v)!=1 for v in fields.values()) or fields['csrf'][0]!=csrf or fields['code'][0] not in CODES:
                self.respond(400,'Invalid camp code or setup form');return
            selected.append(CODES[fields['code'][0]])
            self.respond(200,'<!doctype html><meta http-equiv="refresh" content="4;url=/"><title>Starting camp</title><h1>Starting your camp</h1><p>This page will reopen when the local service is ready.</p>');ready.set()
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler);server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_port}/';print('First-run camp setup:',url,flush=True)
    if open_browser:webbrowser.open(url)
    try:ready.wait()
    finally:server.shutdown();server.server_close()
    return selected[0]
