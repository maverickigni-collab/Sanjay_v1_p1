"""Durable camp geometry, facilities and reviewed reports; no stock invented here."""
from contextlib import contextmanager
import datetime as dt
import getpass
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import uuid
from urllib.parse import urlsplit

from .geography import load_test_geography, DIRECTIONS


FACILITY_TYPES = {'school':'School','hospital':'Hospital','clinic':'Clinic','doctor':'Doctor / medical practice','surgical_center':'Surgical centre','nursing_care':'Nursing care','pharmacy':'Pharmacy / medicines','ambulance':'Ambulance service','first_aid':'First-aid post','rehabilitation':'Rehabilitation','blood_bank':'Blood bank','police':'Police','fire_station':'Fire station','water':'Drinking water','other':'Other'}
MEDICAL_TYPES = ('hospital','clinic','doctor','surgical_center','nursing_care','pharmacy','ambulance','first_aid','rehabilitation','blood_bank')

class RegistryError(ValueError): pass
class Conflict(RegistryError): pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00','Z')


def text(value, name, maximum=200, empty=False):
    if not isinstance(value,str) or len(value)>maximum or ('\x00' in value) or (not empty and not value.strip()):
        raise RegistryError('Invalid '+name)
    return value.strip()


def coordinate(value, limit):
    if type(value) not in (int,float) or not math.isfinite(value) or not -limit<=value<=limit:
        raise RegistryError('Invalid finite coordinate')
    return value


def source_url(value, empty=False):
    text(value,'source URL',1000,empty=empty)
    if value or not empty:
        u=urlsplit(value)
        if u.scheme!='https' or not u.hostname or u.username or u.password:raise RegistryError('Expected public HTTPS source URL')
    return value


def identifier(value):
    try:
        if not isinstance(value,str) or str(uuid.UUID(value))!=value:raise ValueError()
    except (ValueError,AttributeError):raise RegistryError('Expected canonical UUID') from None
    return value


def validate_polygon(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)!={'type','coordinates'} or value['type']!='Polygon':raise RegistryError('Expected GeoJSON Polygon')
    rings=value['coordinates']
    if not isinstance(rings,list) or len(rings)!=1 or not isinstance(rings[0],list):raise RegistryError('One polygon ring required')
    ring=rings[0]
    if not 4<=len(ring)<=129 or ring[0]!=ring[-1]:raise RegistryError('Polygon needs 3–128 vertices and a closed ring')
    for p in ring:
        if not isinstance(p,list) or len(p)!=2:raise RegistryError('Expected longitude, latitude pairs')
        coordinate(p[0],180);coordinate(p[1],90)
    if len({tuple(p) for p in ring[:-1]})!=len(ring)-1:raise RegistryError('Duplicate polygon vertex')
    area=abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(ring,ring[1:])))
    if area<1e-12:raise RegistryError('Polygon has no usable area')
    def orient(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def on(a,b,p):return abs(orient(a,b,p))<1e-12 and min(a[0],b[0])<=p[0]<=max(a[0],b[0]) and min(a[1],b[1])<=p[1]<=max(a[1],b[1])
    edges=list(zip(ring,ring[1:]))
    for i,(a,b) in enumerate(edges):
        for j in range(i+1,len(edges)):
            if j==i+1 or (i==0 and j==len(edges)-1):continue
            c,d=edges[j]
            if (orient(a,b,c)*orient(a,b,d)<0 and orient(c,d,a)*orient(c,d,b)<0) or on(a,b,c) or on(a,b,d) or on(c,d,a) or on(c,d,b):
                raise RegistryError('Self-intersecting polygon')
    return json.loads(json.dumps(value,allow_nan=False))


class Registry:
    def __init__(self,folder):
        self.folder=Path(folder).resolve();fresh=not self.folder.exists();self.folder.mkdir(parents=True,exist_ok=True)
        if fresh:
            if os.name=='nt':subprocess.run(['icacls',str(self.folder),'/inheritance:r','/grant:r',getpass.getuser()+':(OI)(CI)F'],check=True,capture_output=True)
            else:self.folder.chmod(0o700)
        self.path=self.folder/'registry.sqlite'
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS records(kind TEXT NOT NULL,id TEXT NOT NULL,data TEXT NOT NULL,version INTEGER NOT NULL,PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS geometry_history(kind TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,data TEXT NOT NULL,at TEXT NOT NULL,PRIMARY KEY(kind,id,version));
            CREATE TABLE IF NOT EXISTS report_reviews(id TEXT PRIMARY KEY,report_id TEXT NOT NULL,old_status TEXT NOT NULL,new_status TEXT NOT NULL,at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS route_choices(grant_id TEXT PRIMARY KEY,data TEXT NOT NULL);
            INSERT OR IGNORE INTO meta VALUES('report_revision','0');''')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM records WHERE kind='camp'").fetchone():
                if db.execute('SELECT 1 FROM records').fetchone():raise RegistryError('Missing camp registry; inspect existing data')
                fixture=load_test_geography();zones={f['properties']['id']:f['geometry'] for f in fixture['zones']['features']}
                for c in fixture['camps']:
                    record={k:c[k] for k in ('id','name','host_name','short_name','host_type','address','capacity','coordinate_status','permission_status')}
                    record.update(lon=c['coordinates'][0],lat=c['coordinates'][1],perimeter=None,service_area=zones[c['id']],source_url=c['location_source'],version=1,updated_at=now(),test_only=True)
                    self._insert(db,'camp',record)
                    facility=self._facility({'name':c['host_name'],'category':'school' if c['id']=='south' else 'other' if c['id']=='north' else 'hospital','lat':record['lat'],'lon':record['lon'],'source_url':c['location_source'],'note':c['point_note']})
                    self._insert(db,'facility',facility)

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA synchronous=FULL')
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def _insert(db,kind,record):
        db.execute('INSERT INTO records VALUES(?,?,?,?)',(kind,record['id'],json.dumps(record,ensure_ascii=False,allow_nan=False),record['version']))

    @staticmethod
    def _get(db,kind,key):
        row=db.execute('SELECT data FROM records WHERE kind=? AND id=?',(kind,key)).fetchone()
        if not row:raise RegistryError('Unknown '+kind)
        return json.loads(row[0])

    @staticmethod
    def _version(value):
        if type(value) is not int or value<1:raise RegistryError('Invalid expected version')

    @staticmethod
    def _revise(db,kind,old,new):
        if kind in ('camp','facility'):
            db.execute('INSERT INTO geometry_history VALUES(?,?,?,?,?)',(kind,old['id'],old['version'],json.dumps(old,allow_nan=False),now()))
        new['version']=old['version']+1;new['updated_at']=now()
        changed=db.execute('UPDATE records SET data=?,version=? WHERE kind=? AND id=? AND version=?',(json.dumps(new,ensure_ascii=False,allow_nan=False),new['version'],kind,old['id'],old['version'])).rowcount
        if changed!=1:raise Conflict('Record changed; reload before saving')
        return new

    def snapshot(self,include_contact=False):
        with self.connect() as db:
            db.execute('BEGIN')
            result={'camps':[],'facilities':[],'reports':[]}
            for row in db.execute('SELECT kind,data FROM records ORDER BY kind,id'):
                record=json.loads(row['data'])
                if row['kind']=='report' and not include_contact:record.pop('reporter_contact',None)
                result[{'camp':'camps','facility':'facilities','report':'reports'}[row['kind']]].append(record)
            result['report_revision']=int(db.execute("SELECT value FROM meta WHERE key='report_revision'").fetchone()[0])
            result['camps'].sort(key=lambda c:DIRECTIONS.index(c['id']))
            return result

    def get_camp(self,camp_id):
        with self.connect() as db:return self._get(db,'camp',camp_id)

    def update_camp(self,camp_id,changes,expected_version):
        self._version(expected_version)
        allowed={'name','host_name','short_name','host_type','address','lon','lat','perimeter','service_area','capacity','source_url','coordinate_status','permission_status'}
        if not isinstance(changes,dict) or not changes or not set(changes)<=allowed:raise RegistryError('Invalid camp fields')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self._get(db,'camp',camp_id)
            if old['version']!=expected_version:raise Conflict('Camp changed; reload before saving')
            new={**old,**changes}
            for key in ('name','host_name','short_name','host_type','address'):new[key]=text(new[key],key,300)
            coordinate(new['lon'],180);coordinate(new['lat'],90)
            new['perimeter']=validate_polygon(new['perimeter']);new['service_area']=validate_polygon(new['service_area'])
            if new['service_area'] is None:raise RegistryError('Service area required')
            if new['capacity'] is not None and (type(new['capacity']) is not int or not 0<=new['capacity']<=10000000):raise RegistryError('Invalid capacity')
            source_url(new['source_url'])
            if new['coordinate_status'] not in ('provisional','source_checked','field_checked') or new['permission_status'] not in ('not_requested','requested','granted','denied'):raise RegistryError('Invalid status')
            return self._revise(db,'camp',old,new)

    @staticmethod
    def _facility(fields,old=None):
        allowed={'name','category','lat','lon','source_url','verification_status','note','active'}
        if not isinstance(fields,dict) or not fields or not set(fields)<=allowed:raise RegistryError('Invalid facility fields')
        data={**({'source_url':'','verification_status':'unverified','note':'','active':True} if old is None else old),**fields}
        for key in ('name','category','lat','lon'):
            if key not in data:raise RegistryError('Missing facility '+key)
        data['name']=text(data['name'],'facility name',200);data['note']=text(data['note'],'note',1000,True)
        if data['category'] not in FACILITY_TYPES or data['verification_status'] not in ('unverified','source_checked','field_checked') or type(data['active']) is not bool:raise RegistryError('Invalid facility status/category')
        coordinate(data['lat'],90);coordinate(data['lon'],180);source_url(data['source_url'],True)
        if data['verification_status']!='unverified' and not data['source_url']:raise RegistryError('Checked facilities need a source')
        if old is None:data.update(id=str(uuid.uuid4()),version=1,updated_at=now())
        return data

    def add_facility(self,fields):
        record=self._facility(fields)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT COUNT(*) FROM records WHERE kind='facility'").fetchone()[0]>=10000:raise RegistryError('Facility limit reached')
            self._insert(db,'facility',record)
        return record

    def update_facility(self,facility_id,changes,expected_version):
        identifier(facility_id);self._version(expected_version)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self._get(db,'facility',facility_id)
            if old['version']!=expected_version:raise Conflict('Facility changed; reload before saving')
            return self._revise(db,'facility',old,self._facility(changes,old))

    def import_facilities(self,records,source_hash):
        prepared=[self._facility(fields) for fields in records]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM meta WHERE key='facility_import'").fetchone():return 0
            sources={json.loads(r[0])['source_url'] for r in db.execute("SELECT data FROM records WHERE kind='facility'")}
            added=0
            for record in prepared:
                if record['source_url'] in sources:continue
                self._insert(db,'facility',record);sources.add(record['source_url']);added+=1
            db.execute("INSERT INTO meta VALUES('facility_import',?)",(source_hash,))
            return added

    def submit_report(self,fields,*,source='phone',camp_id=None):
        required={'category','description','lat','lon','severity','reporter_contact'}
        if not isinstance(fields,dict) or set(fields)!=required:raise RegistryError('Invalid report fields')
        if source not in ('phone','camp') or (camp_id not in DIRECTIONS if source=='camp' else camp_id is not None):raise RegistryError('Invalid report intake source/camp')
        record=dict(fields)
        if record['category'] not in ('road_obstruction','water','medical','supply','other') or record['severity'] not in ('unknown','moderate','high','critical'):raise RegistryError('Invalid report category/severity')
        record['description']=text(record['description'],'description',1500)
        record['reporter_contact']=text(record['reporter_contact'],'contact',150,True)
        coordinate(record['lat'],90);coordinate(record['lon'],180)
        record.update(id=str(uuid.uuid4()),version=1,status='unverified',created_at=now(),updated_at=now(),source=source,camp_id=camp_id)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT COUNT(*) FROM records WHERE kind='report'").fetchone()[0]>=10000:raise RegistryError('Report limit reached; archive explicitly')
            self._insert(db,'report',record);db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='report_revision'")
        return record

    def get_report(self,report_id,include_contact=False):
        identifier(report_id)
        with self.connect() as db:record=self._get(db,'report',report_id)
        if not include_contact:record.pop('reporter_contact',None)
        return record

    def review_report(self,report_id,status,expected_version):
        identifier(report_id);self._version(expected_version)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self._get(db,'report',report_id)
            if old['version']!=expected_version:raise Conflict('Report changed; reload before reviewing')
            if status not in {'unverified':('confirmed','rejected'),'confirmed':('resolved','rejected')}.get(old['status'],()):raise RegistryError('Invalid report transition')
            new=self._revise(db,'report',old,{**old,'status':status})
            db.execute('INSERT INTO report_reviews VALUES(?,?,?,?,?)',(str(uuid.uuid4()),report_id,old['status'],status,now()))
            db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='report_revision'")
            return new

    def save_route_choice(self,grant_id,choice):
        identifier(grant_id)
        if not isinstance(choice,dict):raise RegistryError('Invalid route choice')
        raw=json.dumps(choice,allow_nan=False)
        if len(raw.encode())>1000000:raise RegistryError('Route choice too large')
        with self.connect() as db:db.execute('INSERT INTO route_choices VALUES(?,?) ON CONFLICT(grant_id) DO UPDATE SET data=excluded.data',(grant_id,raw))

    def get_route_choice(self,grant_id):
        identifier(grant_id)
        with self.connect() as db:row=db.execute('SELECT data FROM route_choices WHERE grant_id=?',(grant_id,)).fetchone()
        return json.loads(row[0]) if row else None
