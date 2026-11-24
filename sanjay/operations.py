"""Four independent local test writers. Existing A/B identities are never loaded."""
import hashlib
import os
import datetime as dt
import json
from pathlib import Path
import threading
import time
import uuid

from .geography import load_test_geography,point_inside
from .identities import CAMP_NODES
from .registry import Registry,RegistryError,Conflict,now,FACILITY_TYPES,MEDICAL_TYPES
from .routing import Routes,nearby_reports,distance
from .store import Store

DATA=Path(__file__).resolve().parent.parent/'data/map/operations'

class Operations:
    def __init__(self,folder,data=DATA,local_camp=None):
        self._owner=None;self.session_id=uuid.uuid4().hex
        self.registry=Registry(folder);self.lock=threading.RLock();self.nodes={}
        self._owner=(self.registry.folder/'.operator.lock').open('a+b')
        if self._owner.tell()==0:self._owner.write(b'0');self._owner.flush()
        self._owner.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self._owner.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self._owner.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.close();raise RegistryError('This operations root already has a writer; use its existing service') from None
        with self.registry.connect() as db:
            provisioned=db.execute("SELECT 1 FROM meta WHERE key='nodes_provisioned'").fetchone()
        with self.registry.connect() as db:
            saved=db.execute("SELECT value FROM meta WHERE key='local_camp'").fetchone()
        if saved:
            if local_camp is not None and local_camp!=saved[0]:raise RegistryError('This install already owns another camp; never rename an active identity')
            local_camp=saved[0]
        elif provisioned and local_camp is not None:raise RegistryError('Existing four-camp demo cannot be converted; use a new independent root')
        if local_camp is not None and local_camp not in CAMP_NODES:raise RegistryError('Unknown camp setup code')
        self.local_camp=local_camp;names=(local_camp,) if local_camp else CAMP_NODES
        if provisioned and any(not (self.registry.folder/name).exists() for name in names):
            self.close();raise RegistryError('Provisioned node missing; stop and inspect, never recreate its identity')
        for name in names:
            path=self.registry.folder/name
            self.nodes[name]=Store(path) if path.exists() else Store.create(path,name)
            if self.nodes[name].node!=name:raise RegistryError('Independent node folder/identity mismatch')
        for node in self.nodes.values():
            for peer in self.nodes.values():node.trust((peer.folder/'public.json').read_bytes())
        with self.registry.connect() as db:
            if local_camp:db.execute("INSERT OR IGNORE INTO meta VALUES('local_camp',?)",(local_camp,))
            db.execute("INSERT OR IGNORE INTO meta VALUES('nodes_provisioned','1')")
        self.data=Path(data);self.routing=None;self.data_error=''
        try:
            manifest=json.loads((self.data/'manifest.json').read_text(encoding='utf-8'))
            raw=(self.data/manifest['facilities']['file']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=manifest['facilities']['sha256']:raise RegistryError('Facility source hash mismatch')
            records=json.loads(raw)
            if isinstance(records,dict):records=records['facilities']
            self.registry.import_facilities(records,manifest['facilities']['sha256'])
            self.routing=Routes(self.data)
        except (OSError,ValueError,KeyError) as exc:self.data_error=str(exc)
        self.sync()
        from .camp_links import CampLinks
        self.links=CampLinks(self)
        if self.local_camp:self.links.start()

    def close(self):
        if getattr(self,'links',None):self.links.close()
        if getattr(self,'_owner',None) is not None:self._owner.close();self._owner=None

    def __del__(self):self.close()

    def sync(self):
        with self.lock:
            bundles=[node.export_bundle() for node in self.nodes.values()]
            for node in self.nodes.values():
                for raw in bundles:node.import_bundle(raw)

    def snapshot(self,camp=None):
        camp=camp or self.local_camp or 'south'
        if camp not in self.nodes:raise RegistryError('Unknown acting camp')
        with self.lock:
            snap=self.nodes[camp].snapshot();snap['registry']=self.registry.snapshot();snap['data_error']=self.data_error;snap['facility_types']=FACILITY_TYPES;snap['medical_facility_types']=MEDICAL_TYPES;snap['local_camp']=self.local_camp;snap['actor_nodes']=list(self.nodes)
            geo=load_test_geography();zones={f['properties']['id']:f for f in geo['zones']['features']}
            for c in geo['camps']:
                record=next(r for r in snap['registry']['camps'] if r['id']==c['id'])
                c.update(record);c['coordinates']=[record['lon'],record['lat']];c['location_source']=record['source_url'];c['ledger_origin']=record['id']
                zone=zones[c['id']];zone['geometry']=record['service_area']
                ring=record['service_area']['coordinates'][0];centre=[sum(p[i] for p in ring[:-1])/(len(ring)-1) for i in (0,1)]
                zone['properties']['label_position']=centre if point_inside(centre,ring) else ring[0]
            snap['geography']=geo
            return snap

    def facilities(self,camp,query='',category=''):
        centre=self.registry.get_camp(camp);ring=centre['service_area']['coordinates'][0];result=[]
        for f in self.registry.snapshot()['facilities']:
            if not f['active'] or (category and f['category']!=category) or query.casefold() not in (f['name']+' '+f['note']).casefold():continue
            result.append({**f,'inside_service_area':point_inside([f['lon'],f['lat']],ring),'straight_line_m':round(distance((centre['lat'],centre['lon']),(f['lat'],f['lon']))),'nearby':distance((centre['lat'],centre['lon']),(f['lat'],f['lon']))<=3000})
        return sorted(result,key=lambda f:(not f['inside_service_area'],f['straight_line_m'],f['id']))

    def routes(self,camp,grant_id=None,destination=None):
        if self.routing is None:raise RegistryError('Offline road data unavailable: '+self.data_error)
        state=self.nodes[camp].snapshot()['state']
        if grant_id:
            grant=state['grants'].get(grant_id)
            if not grant or grant['supplier']!=camp or grant['status']!='approved':raise RegistryError('Select an approved allocation at its supplying camp')
            destination=state['requests'][grant['request_id']]['owner']
        if destination not in CAMP_NODES or destination==camp:raise RegistryError('Select a different destination camp')
        with self.lock:return self.routing.options(self.registry.get_camp(camp),self.registry.get_camp(destination),self.registry.snapshot()['reports'])

    def choose_route(self,camp,grant_id,route_id,acknowledge):
        if type(acknowledge) is not bool:raise RegistryError('Explicit warning acknowledgement required')
        with self.lock:
            if self.routing is None:raise RegistryError('Offline routing unavailable')
            route=self.routing.cache.get(route_id)
            if not route:raise RegistryError('Route expired; calculate again')
            state=self.nodes[camp].snapshot()['state'];grant=state['grants'].get(grant_id)
            if not grant or grant['status']!='approved' or grant['supplier']!=camp or route['from']!=camp or state['requests'][grant['request_id']]['owner']!=route['to']:raise RegistryError('Route does not match approved allocation')
            if route['camp_versions']!=[self.registry.get_camp(route['from'])['version'],self.registry.get_camp(route['to'])['version']]:raise Conflict('Camp changed; calculate route again')
            warnings=nearby_reports(route['geometry']['coordinates'],self.registry.snapshot()['reports'])
            if warnings!=route['warnings']:raise Conflict('Relevant reports changed; calculate and review again')
            if not acknowledge:raise RegistryError('Acknowledge advisory limits and review nearby reports first')
            self.registry.save_route_choice(grant_id,{**route,'acknowledged':acknowledge,'selected_at':now(),'session_id':self.session_id,'selected_monotonic':time.monotonic()})

    def command(self,camp,kind,fields):
        if camp not in self.nodes or not isinstance(fields,dict):raise RegistryError('Invalid acting camp/fields')
        with self.lock:
            self.sync();payload=dict(fields)
            if kind=='dispatch':
                choice=self.registry.get_route_choice(payload.get('grant_id'))
                if not choice or self.routing is None or choice['from']!=camp or choice['graph_sha256']!=self.routing.sha:raise RegistryError('Choose an offline road option before departure')
                if choice.get('session_id')!=self.session_id:raise RegistryError('Route choice from an earlier session; calculate and review again')
                if not 0<=time.monotonic()-choice.get('selected_monotonic',-1801)<=1800:raise RegistryError('Route choice expired; calculate and review again')
                self.routing.load()
                age=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(choice['selected_at'])).total_seconds()
                if not 0<=age<=1800:raise RegistryError('Route choice expired; calculate and review again')
                if choice['camp_versions']!=[self.registry.get_camp(choice['from'])['version'],self.registry.get_camp(choice['to'])['version']]:raise Conflict('Camp changed; choose a route again')
                if choice['warnings']!=nearby_reports(choice['geometry']['coordinates'],self.registry.snapshot()['reports']):raise Conflict('Relevant reports changed; review route again')
                grant=self.nodes[camp].snapshot()['state']['grants'].get(payload.get('grant_id'))
                if not grant:raise RegistryError('Unknown allocation')
                payload['quantity']=grant['quantity']
            key={'request':'request_id','grant':'grant_id','dispatch':'dispatch_id','receipt':'receipt_id'}.get(kind)
            if key:
                if key in payload:raise RegistryError('New record IDs are server-generated')
                payload[key]=str(uuid.uuid4())
            event=self.nodes[camp].command(kind,payload);self.sync();return event
