"""Stable, signed public camp profiles; explicitly pinned, bounded peer exchange."""
import base64
import hashlib
import json
import re
import shlex
import subprocess
import threading
import zlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey,Ed25519PublicKey
from .store import encode,parse_json,_b64,_unb64,MAX_BYTES,ExchangeError
from .registry import RegistryError,Conflict,coordinate,validate_polygon,text,source_url,now,Registry
from .identities import CAMP_NODES

FIELDS={'id','name','host_name','short_name','host_type','address','capacity','coordinate_status','permission_status','lon','lat','perimeter','service_area','source_url','version','updated_at','test_only'}
CONTEXT=b'SANJAY-CAMP-PROFILE-1\0'
LIMIT=65536

def camp_id(node,public):return 'SJ1-'+node.upper()+'-'+hashlib.sha256(_unb64(public)).hexdigest()[:16].upper()

def unpack(code):
    if not isinstance(code,str) or len(code)>LIMIT or not code.startswith('SANJAY1.'):raise RegistryError('Paste the full SANJAY1 share code; a short ID alone cannot carry boundaries offline')
    try:
        raw=base64.b64decode(code[8:].encode(),altchars=b'-_',validate=True)
        decoder=zlib.decompressobj();raw=decoder.decompress(raw,LIMIT+1)
        if len(raw)>LIMIT or not decoder.eof or decoder.unused_data:raise ValueError('Invalid compressed profile')
        envelope=parse_json(raw)
        if set(envelope)!={'profile','signature'}:raise ValueError('Unexpected profile envelope')
        p=envelope['profile']
        if set(p)!={'v','node','public_key','camp'} or type(p['v']) is not int or p['v']!=1 or p['node'] not in CAMP_NODES:raise ValueError('Unsupported profile')
        c=p['camp']
        if not isinstance(c,dict) or set(c)!=FIELDS or c['id']!=p['node']:raise ValueError('Invalid public camp fields')
        for k in ('name','host_name','short_name','host_type','address'):text(c[k],k,300)
        coordinate(c['lat'],90);coordinate(c['lon'],180)
        validate_polygon(c['perimeter'])
        if validate_polygon(c['service_area']) is None:raise ValueError('Responsibility polygon required')
        if type(c['version']) is not int or c['version']<1 or type(c['test_only']) is not bool:raise ValueError('Invalid profile revision')
        if c['capacity'] is not None and (type(c['capacity']) is not int or not 0<=c['capacity']<=10000000):raise ValueError('Invalid capacity')
        if c['coordinate_status'] not in ('provisional','source_checked','field_checked') or c['permission_status'] not in ('not_requested','requested','granted','denied'):raise ValueError('Invalid source status')
        source_url(c['source_url']);text(c['updated_at'],'updated time',40)
        public=_unb64(p['public_key']);signature=_unb64(envelope['signature'])
        Ed25519PublicKey.from_public_bytes(public).verify(signature,CONTEXT+encode(p))
        return p
    except (ValueError,TypeError,KeyError,zlib.error,InvalidSignature,RecursionError) as exc:raise RegistryError('Invalid or tampered camp profile') from exc

def ssh_exchange(argv,packet,timeout=40):
    """Bound stdin, stdout and total time, including a peer that never reads."""
    if len(packet)>MAX_BYTES:raise RegistryError('Peer packet exceeds 1 MB; no transfer attempted')
    process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    output=bytearray();overflow=[]
    def read():
        try:
            while True:
                chunk=process.stdout.read(65536)
                if not chunk:break
                output.extend(chunk)
                if len(output)>MAX_BYTES:overflow.append(True);process.kill();break
        except OSError:pass
    def write():
        try:process.stdin.write(packet);process.stdin.close()
        except OSError:pass
        finally:
            try:process.stdin.close()
            except OSError:pass
    reader=threading.Thread(target=read,daemon=True);writer=threading.Thread(target=write,daemon=True)
    reader.start();writer.start()
    try:
        try:process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:raise RegistryError('SSH exchange timed out; no successful sync claimed') from None
        reader.join(timeout=3);writer.join(timeout=3)
        if overflow:raise RegistryError('SSH response exceeds 1 MB; no successful sync claimed')
        if reader.is_alive() or writer.is_alive():raise RegistryError('SSH stream did not close; no successful sync claimed')
        try:diagnostic=parse_json(bytes(output))
        except ValueError:diagnostic=None
        if isinstance(diagnostic,dict) and set(diagnostic)=={'error'}:
            raise RegistryError('Remote camp: '+str(diagnostic['error'])[:300])
        if process.returncode:raise RegistryError('SSH transport failed; check verified host key, restricted key and running remote app')
        return bytes(output)
    finally:
        if process.poll() is None:process.kill();process.wait()
        writer.join(timeout=3);reader.join(timeout=3)
        if not writer.is_alive():process.stdin.close()
        if not reader.is_alive():process.stdout.close()

class CampLinks:
    def __init__(self,ops):
        self.ops=ops;self.stop=threading.Event();self.sync_lock=threading.Lock();self.thread=None;self.last_error='';self.last_sync=None
        with ops.registry.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS peer_profiles(node TEXT PRIMARY KEY,public_key TEXT UNIQUE NOT NULL,revision INTEGER NOT NULL,code TEXT NOT NULL)')
    def own(self,camp=None):
        camp=camp or self.ops.local_camp
        if camp not in self.ops.nodes:raise RegistryError('Choose a local camp')
        node=self.ops.nodes[camp];identity=parse_json((node.folder/'public.json').read_bytes())
        profile={'v':1,'node':camp,'public_key':identity['public_key'],'camp':{k:v for k,v in self.ops.registry.get_camp(camp).items() if k in FIELDS}}
        key=Ed25519PrivateKey.from_private_bytes((node.folder/'private.key').read_bytes())
        with node.connect() as db:pinned=db.execute('SELECT public FROM trusted WHERE node=?',(camp,)).fetchone()[0]
        if identity.get('node')!=camp or key.public_key().public_bytes_raw()!=pinned or _b64(pinned)!=profile['public_key']:raise RegistryError('Local signing identity mismatch; stop and inspect without replacing keys')
        envelope={'profile':profile,'signature':_b64(key.sign(CONTEXT+encode(profile)))}
        code='SANJAY1.'+base64.urlsafe_b64encode(zlib.compress(encode(envelope),9)).decode()
        if len(code)>LIMIT:raise RegistryError('Public profile too large')
        return {'id':camp_id(camp,profile['public_key']),'code':code,'profile':profile}
    def preview(self,code):
        p=unpack(code)
        return {'id':camp_id(p['node'],p['public_key']),'profile':p,'fingerprint':hashlib.sha256(_unb64(p['public_key'])).hexdigest()}
    def accept(self,code,confirmed_key):
        p=unpack(code);node=p['node']
        if p['public_key']!=confirmed_key:raise RegistryError('Confirm the full public key against the source camp')
        if node in self.ops.nodes:raise Conflict('This install already owns that origin. Use independent single-camp installs, never clone or replace keys')
        with self.ops.lock,self.ops.registry.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT * FROM peer_profiles WHERE node=?',(node,)).fetchone()
            if old:
                if old['public_key']!=p['public_key']:raise Conflict('Pinned camp key cannot be replaced')
                if p['camp']['version']<old['revision']:raise Conflict('Older camp profile rejected')
                if p['camp']['version']==old['revision']:
                    if unpack(old['code'])!=p:raise Conflict('Same revision has conflicting camp data')
                    return {'saved':False,'id':camp_id(node,p['public_key'])}
            for local in self.ops.nodes.values():
                with local.connect() as trusted:
                    reuse=trusted.execute('SELECT node FROM trusted WHERE public=? AND node!=?',(_unb64(p['public_key']),node)).fetchone()
                    if reuse:raise Conflict('A signing key cannot represent two different camp origins')
            current=self.ops.registry._get(db,'camp',node)
            # Pin only after explicit source-key confirmation; event imports never establish trust.
            for local in self.ops.nodes.values():local.trust(encode({'node':node,'public_key':p['public_key']}))
            Registry._revise(db,'camp',current,dict(p['camp']))
            db.execute('INSERT INTO peer_profiles VALUES(?,?,?,?) ON CONFLICT(node) DO UPDATE SET revision=excluded.revision,code=excluded.code',(node,p['public_key'],p['camp']['version'],code))
        return {'saved':True,'id':camp_id(node,p['public_key'])}
    def require_independent(self):
        if not self.ops.local_camp or len(self.ops.nodes)!=1:raise RegistryError('SSH sync requires an independent install: operations --root NEW_FOLDER --camp south (or north/east/west). Existing demo data is retained separately')
    def packet(self):
        self.require_independent()
        with self.ops.lock:
            packet={'code':self.own()['code'],'bundle':_b64(self.ops.nodes[self.ops.local_camp].export_bundle())}
            if len(encode(packet))>MAX_BYTES:raise RegistryError('Peer packet exceeds 1 MB; no partial transfer')
            return packet
    def receive(self,packet):
        self.require_independent()
        if not isinstance(packet,dict) or set(packet)!={'code','bundle'}:raise RegistryError('Invalid peer packet')
        p=unpack(packet['code']);raw=_unb64(packet['bundle'])
        if len(raw)>MAX_BYTES:raise RegistryError('Signed bundle exceeds 1 MB')
        with self.ops.lock,self.ops.registry.connect() as db:
            peer=db.execute('SELECT code FROM peer_profiles WHERE node=?',(p['node'],)).fetchone()
            if not peer or unpack(peer['code'])!=p:raise Conflict('Peer is not pinned or its profile changed; review and accept its current code first')
            self.ops.nodes[self.ops.local_camp].import_bundle(raw)
            response=self.packet()
            self.last_sync=now();self.last_error=''
        return response
    def status(self,camp=None):
        with self.ops.registry.connect() as db:
            peers=[self.preview(r[0]) for r in db.execute('SELECT code FROM peer_profiles ORDER BY node')]
            row=db.execute("SELECT value FROM meta WHERE key='ssh_peer'").fetchone()
        return {**self.own(camp),'independent':bool(self.ops.local_camp),'peers':peers,'ssh':json.loads(row[0]) if row else None,'last_sync':self.last_sync,'last_error':self.last_error,'notice':'Recorded geography, not a survey. Undrawn perimeters remain unknown. Last received state is not guaranteed current.'}
    def configure(self,fields):
        self.require_independent()
        if not isinstance(fields,dict) or set(fields)!={'host','user','port','remote_path','remote_port','enabled'}:raise RegistryError('Invalid SSH settings')
        if not isinstance(fields['host'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]{0,252}',fields['host']):raise RegistryError('Invalid SSH host')
        if not isinstance(fields['user'],str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,31}',fields['user']):raise RegistryError('Invalid SSH user')
        if any(type(fields[k]) is not int or not 1<=fields[k]<=65535 for k in ('port','remote_port')) or type(fields['enabled']) is not bool:raise RegistryError('Invalid SSH ports/enabled')
        path=fields['remote_path']
        if not isinstance(path,str) or not path.startswith('/') or len(path)>500 or any(c in path for c in '\r\n\0'):raise RegistryError('Remote Linux app path must be absolute')
        with self.ops.registry.connect() as db:db.execute("INSERT INTO meta VALUES('ssh_peer',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(fields),))
        self.start();return {'saved':True}
    def sync_once(self):
        self.require_independent()
        if not self.sync_lock.acquire(blocking=False):raise RegistryError('Sync already running')
        try:
            with self.ops.registry.connect() as db:row=db.execute("SELECT value FROM meta WHERE key='ssh_peer'").fetchone()
            if not row:raise RegistryError('Configure SSH first')
            cfg=json.loads(row[0]);key=self.ops.registry.folder/'.ssh'/'transport_ed25519'
            if not key.is_file():raise RegistryError('Provision this install’s restricted SSH transport key first')
            command=shlex.join(['python3',cfg['remote_path']+'/tools/ssh_bridge.py','--port',str(cfg['remote_port'])])
            argv=['ssh','-T','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8','-i',str(key),'-p',str(cfg['port']),'-l',cfg['user'],cfg['host'],command]
            # The authenticated peer is still bounded, including blocked writes and diagnostic frames.
            output=ssh_exchange(argv,encode(self.packet()))
            self.receive(parse_json(output))
            self.last_sync=now();self.last_error='';return {'synced':True,'at':self.last_sync}
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            self.last_error=str(exc);raise RegistryError(self.last_error) from exc
        finally:self.sync_lock.release()
    def start(self):
        if self.thread:return
        def run():
            while not self.stop.wait(15):
                with self.ops.registry.connect() as db:row=db.execute("SELECT value FROM meta WHERE key='ssh_peer'").fetchone()
                if row and json.loads(row[0])['enabled']:
                    try:self.sync_once()
                    except RegistryError:pass
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()
    def close(self):self.stop.set()
