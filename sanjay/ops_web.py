"""Separate loopback operator and invite-only, submission-only phone services."""
from collections import defaultdict,deque
import datetime as dt
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import hashlib
import os
import ipaddress
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs,urlencode,urlsplit

from .ops_ui import phone_page
from .hotspot import Hotspot,HotspotError
from .captive import CaptivePortal
from .registry import RegistryError,Conflict
from .store import ExchangeError,parse_json
from .ui import render
from .web import ASSETS

STATIC=Path(__file__).with_name('static')
EXTRA={'/assets/facility-icons.svg':('facility-icons.svg','image/svg+xml'),'/assets/interactions.js':('interactions.js','text/javascript'),'/assets/interactions.css':('interactions.css','text/css'),'/assets/ops.css':('ops.css','text/css'),'/assets/ops.js':('ops.js','text/javascript'),'/assets/phone.js':('phone.js','text/javascript')}
PHONE_ASSETS={'/assets/location-picker.js','/assets/maps/road-detail.json','/assets/app.css','/assets/ops.css','/assets/phone.js','/assets/leaflet/leaflet.js','/assets/leaflet/leaflet.css','/assets/maps/satellite.json','/assets/maps/vadodara-sentinel.jpg'}


def equal(a,b):return secrets.compare_digest(a.encode('utf-8'),b.encode('utf-8'))


class Invitation:
    def __init__(self):self.lock=threading.RLock();self.rotate();self.hits=defaultdict(deque)
    def rotate(self):
        with self.lock:
            self.token=secrets.token_urlsafe(32);self.deadline=time.monotonic()+28800;self.tickets={}
            self.expires=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=8)).isoformat()
    def live(self):return time.monotonic()<self.deadline
    def ticket(self):
        with self.lock:
            self.tickets={k:v for k,v in self.tickets.items() if v>time.monotonic()}
            if len(self.tickets)>=1024:raise RegistryError('Too many open reporting forms')
            key=secrets.token_urlsafe(24);self.tickets[key]=time.monotonic()+900;return key
    def limited(self,ip):
        with self.lock:
            if ip not in self.hits and len(self.hits)>=1024:return True
            times=self.hits[ip];now=time.monotonic()
            while times and times[0]<now-60:times.popleft()
            if len(times)>=10:return True
            times.append(now);return False


def make_services(ops,port=0,report_port=0,report_host='127.0.0.1',phone_ip='127.0.0.1'):
    invite=Invitation();phone_csrf=secrets.token_urlsafe(32);operator_csrf=secrets.token_urlsafe(32)
    phone_server=None
    network=Hotspot(ops.registry.folder);portal=None;network_lock=threading.RLock()
    def phone_address():
        state=network.cached
        if state.get("state")=="On" and state.get("owned") and state.get("addresses"):return state["addresses"][0]
        return phone_ip
    def invite_url():return f"http://{phone_address()}:{phone_server.server_port}/?invite={invite.token}"
    def handler(mobile):
        csrf=phone_csrf if mobile else operator_csrf
        class Handler(BaseHTTPRequestHandler):
            def setup(self):super().setup();self.connection.settimeout(10)
            def log_message(self,*_):pass
            def reply(self,status,body,mime='application/json',headers=None):
                raw=body if isinstance(body,bytes) else (json.dumps(body,allow_nan=False).encode() if mime=='application/json' else str(body).encode())
                self.send_response(status)
                for k,v in {'Content-Type':mime,'Content-Length':str(len(raw)),'Cache-Control':'no-store','Referrer-Policy':'same-origin','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'nonce-"+csrf+"'; connect-src 'self'; img-src 'self' data:"+('' if mobile else ' https://tile.openstreetmap.org')+"; frame-src https://www.google.com; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",**(headers or {})}.items():self.send_header(k,v)
                self.end_headers();self.wfile.write(raw)
            def hosts(self):return {f'{h}:{self.server.server_port}' for h in (('127.0.0.1','localhost',phone_ip,phone_address()) if mobile else ('127.0.0.1','localhost'))}
            def valid_host(self):return self.headers.get('Host') in self.hosts()
            def authorised(self):
                if not self.valid_host() or (mobile and not invite.live()):return False
                if not mobile:
                    return ipaddress.ip_address(self.client_address[0]).is_loopback and self.headers.get('Sec-Fetch-Site')!='cross-site'
                prefix=f'sanjay_{self.server.server_port}='
                token=invite.token
                return any(p.strip().startswith(prefix) and equal(p.strip()[len(prefix):],token) for p in self.headers.get('Cookie','').split(';'))
            def query(self):
                parsed=urlsplit(self.path);q=parse_qs(parsed.query)
                if any(len(v)!=1 for v in q.values()):raise RegistryError('Repeated query field')
                return parsed.path,{k:v[0] for k,v in q.items()}
            def do_GET(self):
                try:
                    path,q=self.query()
                    if not self.valid_host():self.reply(403,{'error':'Host rejected'});return
                    login=q.get('invite','');token=invite.token
                    if mobile and path=='/' and equal(login,token) and invite.live():
                        cookie={'Set-Cookie':f'sanjay_{self.server.server_port}={token}; HttpOnly; SameSite=Strict; Path=/'}
                        # Cross-site QR redirects can withhold Strict cookies until a new navigation.
                        self.reply(200,phone_page(csrf,invite.ticket()),'text/html; charset=utf-8',cookie)
                        return
                    if not self.authorised():self.reply(403,{'error':'Refresh Phone invitation on the laptop and scan the current QR; this invitation is invalid or expired.' if mobile else 'Only same-site localhost access is allowed'});return
                    camp=q.get('camp',ops.local_camp or 'south')
                    if camp not in ops.nodes:raise RegistryError('Unknown acting camp')
                    if not mobile and path=='/api/instance':
                        root_id=hashlib.sha256(os.path.normcase(str(ops.registry.folder.resolve())).encode()).hexdigest()
                        self.reply(200,{'root_id':root_id,'node':ops.local_camp,'operator_port':self.server.server_port});return
                    if path=='/':
                        body=phone_page(csrf,invite.ticket()) if mobile else render(ops.snapshot(camp),csrf,q.get('message',''),q.get('view','map'))
                        self.reply(200,body,'text/html; charset=utf-8');return
                    if path in {**ASSETS,**EXTRA} and (not mobile or path in PHONE_ASSETS):
                        if path=='/assets/maps/four-camps.json':self.reply(200,ops.snapshot(camp)['geography']);return
                        filename,mime={**ASSETS,**EXTRA}[path];self.reply(200,(STATIC/filename).read_bytes(),mime);return
                    if mobile:self.reply(404,{'error':'Submission only; endpoint unavailable'});return
                    if path=='/state':self.reply(200,ops.snapshot(camp))
                    elif path=='/export':self.reply(200,ops.nodes[camp].export_bundle(),'application/json',{'Content-Disposition':f'attachment; filename="sanjay-{camp}-updates.json"'})
                    elif path=='/api/facilities':self.reply(200,ops.facilities(camp,q.get('q','')[:200],q.get('category','')))
                    elif path=='/api/links':self.reply(200,ops.links.status(camp))
                    elif path=='/api/network':
                        state=network.run('status');state['portal_active']=portal is not None;state['configured_ssid']=network.config['ssid'];self.reply(200,state)
                    elif path=='/api/network-qr.svg':
                        import qrcode.image.svg
                        out=io.BytesIO();qrcode.make(network.wifi_payload(),image_factory=qrcode.image.svg.SvgPathImage).save(out);self.reply(200,out.getvalue(),'image/svg+xml')
                    elif path=='/api/report':self.reply(200,ops.registry.get_report(q.get('id'),True))
                    elif path=='/api/routes':self.reply(200,ops.routes(camp,q.get('grant_id'),q.get('destination')))
                    elif path in ('/api/invite','/api/invite.png'):
                        with invite.lock:
                            url=invite_url()
                            if path.endswith('.png'):
                                import qrcode
                                import qrcode.image.svg
                                out=io.BytesIO();qrcode.make(url,image_factory=qrcode.image.svg.SvgPathImage).save(out)
                                self.reply(200,out.getvalue(),'image/svg+xml')
                            else:self.reply(200,{'url':url,'expires_utc':invite.expires,'live':invite.live(),'submission_only':True,'lan_enabled':report_host!='127.0.0.1'})
                    else:self.reply(404,{'error':'Not found'})
                except (RegistryError,ExchangeError,ValueError,KeyError,TypeError) as exc:self.reply(400,{'error':str(exc)})
                except (sqlite3.Error,OSError):self.reply(503,{'error':'Local storage unavailable; no success claimed'})
            def do_POST(self):
                try:
                    path,q=self.query()
                    if not self.authorised():self.reply(403,{'error':'Access denied'});return
                    if self.headers.get('Origin') not in {f'http://{h}' for h in self.hosts()}:self.reply(403,{'error':'Origin rejected'});return
                    if mobile and invite.limited(self.client_address[0]):self.reply(429,{'error':'Limit: 10 submissions/minute'});return
                    maximum=1000000 if not mobile and path in ('/import','/api/peer-exchange') else 131072 if not mobile and path in ('/api/link-preview','/api/link-accept') else 16384
                    lengths=self.headers.get_all('Content-Length',[])
                    if len(lengths)!=1 or not lengths[0].isdigit() or self.headers.get('Transfer-Encoding'):self.reply(400,{'error':'Exactly one bounded Content-Length required'});return
                    length=int(lengths[0])
                    if length>maximum:self.reply(413,{'error':'Request too large'});return
                    raw=self.rfile.read(length)
                    if len(raw)!=length:raise RegistryError('Incomplete request')
                    if path=='/command':
                        if mobile:self.reply(404,{'error':'Submission only'});return
                        values=parse_qs(raw.decode(),strict_parsing=True)
                        if any(len(v)!=1 for v in values.values()):raise RegistryError('Repeated command field')
                        fields={k:v[0] for k,v in values.items()};proof=fields.pop('csrf','')
                    else:fields={} if path=='/import' else parse_json(raw);proof=self.headers.get('X-CSRF-Token','')
                    if not equal(proof,csrf):self.reply(403,{'error':'CSRF rejected'});return
                    if not isinstance(fields,dict):raise RegistryError('Expected object')
                    if not mobile and path in ('/api/link-preview','/api/link-accept','/api/peer-exchange','/api/ssh-config','/api/ssh-sync'):
                        fields.pop('camp',None)
                        if path=='/api/link-preview' and set(fields)=={'code'}:result=ops.links.preview(fields['code'])
                        elif path=='/api/link-accept' and set(fields)=={'code','confirmed_key'}:result=ops.links.accept(fields['code'],fields['confirmed_key'])
                        elif path=='/api/peer-exchange':result=ops.links.receive(fields)
                        elif path=='/api/ssh-config':result=ops.links.configure(fields)
                        elif path=='/api/ssh-sync' and not fields:result=ops.links.sync_once()
                        else:raise RegistryError('Invalid camp-link action fields')
                        self.reply(200,result);return
                    if not mobile and path=='/api/network':
                        fields.pop('camp',None)
                        if set(fields)!={'action'} or fields['action'] not in ('start','stop'):raise RegistryError('Invalid camp Wi-Fi action')
                        with network_lock:
                            nonlocal portal
                            if fields['action']=='stop':
                                if portal:portal.close();portal=None
                                state=network.run('stop')
                            else:
                                if report_host=='127.0.0.1':raise RegistryError('Restart with --report-host 0.0.0.0 before enabling camp Wi-Fi')
                                state=network.run('start')
                                if not state.get('addresses'):raise RegistryError('Hotspot started; wait a moment and press Start again for its interface address')
                                if not portal:
                                    try:portal=CaptivePortal(state['addresses'][0],invite_url)
                                    except OSError:raise RegistryError('Hotspot is on, but captive DNS/HTTP ports are occupied or blocked; no portal success claimed') from None
                            state['portal_active']=portal is not None;state['configured_ssid']=network.config['ssid']
                            self.reply(200,state);return
                    with ops.lock:
                        if mobile:
                            if path!='/api/reports':self.reply(404,{'error':'Submission only'});return
                            ticket=self.headers.get('X-Form-Ticket','')
                            with invite.lock:
                                if not self.authorised():self.reply(403,{'error':'Invitation expired or revoked before commit'});return
                                if invite.tickets.get(ticket,0)<=time.monotonic():self.reply(403,{'error':'Form expired or already submitted; reload'});return
                                record=ops.registry.submit_report(fields);del invite.tickets[ticket]
                            self.reply(201,{'id':record['id'],'status':'unverified'});return
                        camp=fields.pop('camp',q.get('camp',ops.local_camp or 'south'))
                        if camp not in ops.nodes:raise RegistryError('Unknown acting camp')
                        if path=='/command':
                            kind=fields.pop('kind','');view=fields.pop('return_view','map')
                            if 'quantity' in fields:
                                if not fields['quantity'].isdigit():raise RegistryError('Positive integer quantity required')
                                fields['quantity']=int(fields['quantity'])
                            ops.command(camp,kind,fields)
                            self.reply(303,b'',headers={'Location':'/?'+urlencode({'camp':camp,'view':view,'message':'Saved signed event. Local four-node exchange completed on this computer.'})});return
                        expected={'/api/reports':{'category','description','lat','lon','severity','reporter_contact'},'/api/camp':{'id','changes','version'},'/api/facility':({'id','changes','version'} if 'id' in fields else {'changes'}),'/api/review':{'id','status','version'},'/api/route-choice':{'grant_id','route_id','acknowledge'},'/api/invite/rotate':set()}
                        if path in expected and set(fields)!=expected[path]:raise RegistryError('Unexpected/missing action fields')
                        if path=='/import':result=ops.nodes[camp].import_bundle(raw);ops.sync()
                        elif path=='/api/reports':
                            record=ops.registry.submit_report(fields,source='camp',camp_id=camp)
                            self.reply(201,{k:record[k] for k in ('id','status','source','camp_id')});return
                        elif path=='/api/camp':
                            if ops.local_camp and fields['id']!=ops.local_camp:raise RegistryError('Remote camp geography is read-only; accept a signed profile update')
                            result=ops.registry.update_camp(fields['id'],fields['changes'],fields['version'])
                        elif path=='/api/facility':result=ops.registry.update_facility(fields['id'],fields['changes'],fields['version']) if 'id' in fields else ops.registry.add_facility(fields['changes'])
                        elif path=='/api/review':result=ops.registry.review_report(fields['id'],fields['status'],fields['version']);result.pop('reporter_contact',None)
                        elif path=='/api/route-choice':ops.choose_route(camp,fields['grant_id'],fields['route_id'],fields['acknowledge']);result={'saved':True}
                        elif path=='/api/invite/rotate':invite.rotate();result={'revoked':True}
                        else:self.reply(404,{'error':'Not found'});return
                        self.reply(200,result)
                except Conflict as exc:self.reply(409,{'error':str(exc)})
                except (RegistryError,ExchangeError,ValueError,KeyError,TypeError) as exc:self.reply(400,{'error':str(exc)})
                except (sqlite3.Error,OSError):self.reply(503,{'error':'Local storage unavailable; no success claimed'})
        return Handler
    operator=ThreadingHTTPServer(('127.0.0.1',port),handler(False));operator.daemon_threads=True
    try:phone_server=ThreadingHTTPServer((report_host,report_port),handler(True));phone_server.daemon_threads=True
    except OSError:operator.server_close();raise
    operator.invitation=invite;phone_server.invitation=invite
    return [operator,phone_server],f'http://127.0.0.1:{operator.server_port}/'
