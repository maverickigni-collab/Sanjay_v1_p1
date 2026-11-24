"""Durable, bounded, explicitly trusted signed-event exchange."""
import base64
import datetime as dt
import getpass
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .workflow import MissingDependency, WorkflowError, apply_event, initial_state, validate_payload

MAX_BYTES = 1_000_000
MAX_EVENTS = 500
from .identities import NODES
KINDS = {'stock', 'request', 'grant', 'accept', 'approve', 'dispatch', 'receipt'}


class ExchangeError(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExchangeError('Duplicate JSON key')
        result[key] = value
    return result


def parse_json(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ExchangeError('Bundle exceeds 1 MB or is not bytes')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ExchangeError('Non-finite JSON number')))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ExchangeError('Invalid JSON: ' + str(exc)) from exc


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _uuid(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ExchangeError('IDs must be canonical UUIDs') from None


def validate_event(event):
    if not isinstance(event, dict) or set(event) != {'v', 'id', 'origin', 'seq', 'at', 'kind', 'payload'}:
        raise ExchangeError('Invalid event fields')
    if type(event['v']) is not int or event['v'] != 1 or event['origin'] not in NODES:
        raise ExchangeError('Unsupported version or origin')
    if type(event['seq']) is not int or not 1 <= event['seq'] <= 1_000_000:
        raise ExchangeError('Invalid origin sequence')
    _uuid(event['id'])
    if not isinstance(event['kind'], str) or event['kind'] not in KINDS or not isinstance(event['payload'], dict):
        raise ExchangeError('Invalid event kind/payload')
    try:
        validate_payload(event['kind'], event['payload'])
    except WorkflowError as exc:
        raise ExchangeError('Invalid payload schema: ' + str(exc)) from exc
    for name, value in event['payload'].items():
        if name.endswith('_id'):
            _uuid(value)
    try:
        stamp = event['at']
        if not isinstance(stamp, str) or len(stamp) > 40 or not stamp.endswith('Z'):
            raise ValueError()
        dt.datetime.fromisoformat(stamp[:-1] + '+00:00')
    except (ValueError, TypeError):
        raise ExchangeError('Expected UTC event time') from None


def _b64(raw):
    return base64.b64encode(raw).decode('ascii')


def _unb64(text):
    try:
        if not isinstance(text, str):
            raise ValueError()
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise ExchangeError('Invalid base64') from exc


class Store:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.db_path = self.folder / 'node.sqlite'
        if not self.db_path.is_file():
            raise ExchangeError('Node not initialised; use init first')
        with self.connect() as db:
            meta = dict(db.execute('SELECT key,value FROM meta'))
        self.node = meta['node']
        self.read_only = meta.get('recovery', '0') == '1'

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @classmethod
    def create(cls, folder, node):
        if node not in NODES:
            raise ExchangeError('Unsupported independent camp identity')
        path = Path(folder).resolve()
        path.mkdir(parents=True, exist_ok=False)
        # Restrict the new private node directory before writing its key.
        if os.name == 'nt':
            subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                            getpass.getuser() + ':(OI)(CI)F'], check=True, capture_output=True)
        else:
            path.chmod(0o700)
        key = Ed25519PrivateKey.generate()
        private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        keyfile = path / 'private.key'
        with keyfile.open('xb') as out:
            out.write(private)
        if os.name != 'nt':
            keyfile.chmod(0o600)
        with closing(sqlite3.connect(path / 'node.sqlite')) as db, db:
            db.executescript('''
                CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE trusted(node TEXT PRIMARY KEY,public BLOB NOT NULL);
                CREATE TABLE events(
                    position INTEGER PRIMARY KEY,
                    id TEXT UNIQUE NOT NULL, origin TEXT NOT NULL,seq INTEGER NOT NULL,
                    data BLOB NOT NULL,signature BLOB NOT NULL,status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '', applied_order INTEGER UNIQUE,
                    UNIQUE(origin,seq));
            ''')
            db.executemany('INSERT INTO meta VALUES(?,?)', [('node', node), ('recovery', '0')])
            db.execute('INSERT INTO trusted VALUES(?,?)', (node, public))
        (path / 'public.json').write_bytes(encode({'node': node, 'public_key': _b64(public)}))
        return cls(path)

    def _writable(self):
        if self.read_only:
            raise ExchangeError('Recovery copy is inspection-only; never resume a rolled-back writer')

    def trust(self, raw):
        self._writable()
        value = parse_json(raw)
        if not isinstance(value, dict) or set(value) != {'node', 'public_key'} or value['node'] not in NODES:
            raise ExchangeError('Invalid public identity')
        public = _unb64(value['public_key'])
        if len(public) != 32:
            raise ExchangeError('Invalid public key')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT public FROM trusted WHERE node=?', (value['node'],)).fetchone()
            if old and old[0] != public:
                raise ExchangeError('Pinned key cannot be replaced')
            db.execute('INSERT OR IGNORE INTO trusted VALUES(?,?)', (value['node'], public))

    def _state(self, db):
        state = initial_state()
        for row in db.execute("SELECT data FROM events WHERE status='applied' ORDER BY applied_order"):
            state = apply_event(state, parse_json(bytes(row[0])))
        return state

    def snapshot(self):
        with self.connect() as db:
            db.execute('BEGIN')
            state = self._state(db)
            statuses = []
            for row in db.execute('SELECT id,origin,seq,status,reason,data FROM events ORDER BY position'):
                item = {k: row[k] for k in ('id', 'origin', 'seq', 'status', 'reason')}
                item['kind'] = parse_json(bytes(row['data']))['kind']
                statuses.append(item)
            trust = [r[0] for r in db.execute('SELECT node FROM trusted ORDER BY node')]
        return {'node': self.node, 'read_only': self.read_only, 'state': state, 'events': statuses,
                'trusted_nodes': trust, 'freshness': 'Only received updates shown; current remote status unknown.'}

    def command(self, kind, payload):
        self._writable()
        key = Ed25519PrivateKey.from_private_bytes((self.folder / 'private.key').read_bytes())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            pinned = db.execute('SELECT public FROM trusted WHERE node=?', (self.node,)).fetchone()[0]
            if public != pinned:
                raise ExchangeError('Local key does not match pinned node identity')
            if db.execute("SELECT 1 FROM events WHERE origin=? AND status!='applied'", (self.node,)).fetchone():
                raise ExchangeError('Resolve pending/rejected own-origin events before authoring')
            seq = db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE origin=?', (self.node,)).fetchone()[0]
            event = {'v': 1, 'id': str(uuid.uuid4()), 'origin': self.node, 'seq': seq,
                     'at': dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z'), 'kind': kind, 'payload': payload}
            validate_event(event)
            apply_event(self._state(db), event)
            if db.execute('SELECT COUNT(*) FROM events').fetchone()[0] >= MAX_EVENTS:
                raise ExchangeError('Demo event limit reached; archive, do not delete active history')
            data = encode(event)
            order = db.execute('SELECT COALESCE(MAX(applied_order),0)+1 FROM events').fetchone()[0]
            db.execute("INSERT INTO events(id,origin,seq,data,signature,status,applied_order) VALUES(?,?,?,?,?,'applied',?)",
                       (event['id'], self.node, seq, data, key.sign(data), order))
            self._apply_pending(db)
        return event

    def export_bundle(self):
        with self.connect() as db:
            rows = db.execute('SELECT data,signature FROM events ORDER BY position').fetchall()
        raw = encode({'version': 1, 'events': [{'data': _b64(r[0]), 'signature': _b64(r[1])} for r in rows]})
        if len(raw) > MAX_BYTES:
            raise ExchangeError('Export exceeds demo size limit')
        return raw

    def _apply_pending(self, db):
        state = self._state(db)
        progress = True
        while progress:
            progress = False
            for row in db.execute("SELECT * FROM events WHERE status='pending' ORDER BY position").fetchall():
                expected = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE origin=? AND status='applied'", (row['origin'],)).fetchone()[0]
                if row['seq'] != expected:
                    continue
                try:
                    changed = apply_event(state, parse_json(bytes(row['data'])))
                except MissingDependency as exc:
                    db.execute('UPDATE events SET reason=? WHERE id=?', (str(exc), row['id']))
                    continue
                except WorkflowError as exc:
                    db.execute("UPDATE events SET status='rejected',reason=? WHERE id=?", (str(exc), row['id']))
                    continue
                order = db.execute('SELECT COALESCE(MAX(applied_order),0)+1 FROM events').fetchone()[0]
                db.execute("UPDATE events SET status='applied',reason='',applied_order=? WHERE id=?", (order, row['id']))
                state = changed
                progress = True

    def import_bundle(self, raw):
        self._writable()
        bundle = parse_json(raw)
        if not isinstance(bundle, dict) or set(bundle) != {'version', 'events'} or type(bundle['version']) is not int or bundle['version'] != 1:
            raise ExchangeError('Unsupported bundle')
        if not isinstance(bundle['events'], list) or len(bundle['events']) > MAX_EVENTS:
            raise ExchangeError('Too many events')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            trusted = {r['node']: r['public'] for r in db.execute('SELECT * FROM trusted')}
            checked, ids, seqs = [], {}, {}
            for wrapped in bundle['events']:
                if not isinstance(wrapped, dict) or set(wrapped) != {'data', 'signature'}:
                    raise ExchangeError('Invalid signed event wrapper')
                data, signature = _unb64(wrapped['data']), _unb64(wrapped['signature'])
                event = parse_json(data)
                validate_event(event)
                if event['origin'] not in trusted:
                    raise ExchangeError('Untrusted origin; provision public key locally first')
                try:
                    Ed25519PublicKey.from_public_bytes(trusted[event['origin']]).verify(signature, data)
                except (InvalidSignature, ValueError) as exc:
                    raise ExchangeError('Signature verification failed') from exc
                fingerprint = (data, signature)
                seqkey = (event['origin'], event['seq'])
                if (event['id'] in ids and ids[event['id']] != fingerprint) or (seqkey in seqs and seqs[seqkey] != fingerprint):
                    raise ExchangeError('Event ID or origin-sequence collision in bundle')
                ids[event['id']], seqs[seqkey] = fingerprint, fingerprint
                existing = db.execute('SELECT * FROM events WHERE id=? OR (origin=? AND seq=?)', (event['id'], *seqkey)).fetchall()
                if any((r['data'], r['signature']) != fingerprint for r in existing):
                    raise ExchangeError('Event collision; no bundle changes applied. Retain file for review.')
                checked.append((event, data, signature))
            new_ids = {e['id'] for e, _, _ in checked} - {r[0] for r in db.execute('SELECT id FROM events')}
            if db.execute('SELECT COUNT(*) FROM events').fetchone()[0] + len(new_ids) > MAX_EVENTS:
                raise ExchangeError('Demo history limit reached')
            for event, data, signature in checked:
                db.execute("INSERT OR IGNORE INTO events(id,origin,seq,data,signature,status,reason) VALUES(?,?,?,?,?,'pending','Waiting for origin sequence or dependency')",
                           (event['id'], event['origin'], event['seq'], data, signature))
            self._apply_pending(db)
            return [dict(r) for r in db.execute('SELECT id,status,reason FROM events ORDER BY position')]

    def backup(self, target):
        target = Path(target)
        if target.exists():
            raise ExchangeError('Backup destination exists; refusing overwrite')
        with self.connect() as source, closing(sqlite3.connect(target)) as dest, dest:
            source.backup(dest)
        return target

    @classmethod
    def inspect_backup(cls, backup, destination):
        """Restore only to a new, permanently inspection-only node directory."""
        source_path = Path(backup).resolve()
        if not source_path.is_file():
            raise ExchangeError('Backup does not exist')
        dest = Path(destination).resolve()
        dest.mkdir(parents=True, exist_ok=False)
        with closing(sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(dest / 'node.sqlite')) as db, db:
            if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ExchangeError('Backup integrity failed')
            source.backup(db)
            db.execute("UPDATE meta SET value='1' WHERE key='recovery'")
        return cls(dest)
