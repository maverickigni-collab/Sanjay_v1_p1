"""Bounded OSM car routing; output never establishes current road passability."""
import hashlib
import json
import math
from pathlib import Path
import threading

from .registry import RegistryError


def distance(a,b):
    lat1,lat2=map(math.radians,(a[0],b[0]));dl=math.radians(b[0]-a[0]);dn=math.radians(b[1]-a[1])
    return 6371000*2*math.asin(min(1,math.sqrt(math.sin(dl/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dn/2)**2)))


def nearby_reports(coords,reports):
    """150m straight-line corridor, not proof that a report blocks a road."""
    if len(coords)<2:return []
    south=min(p[1] for p in coords)-0.002;north=max(p[1] for p in coords)+0.002
    west=min(p[0] for p in coords)-0.002;east=max(p[0] for p in coords)+0.002
    found=[]
    for r in reports:
        if r['status'] not in ('unverified','confirmed') or not south<=r['lat']<=north or not west<=r['lon']<=east:continue
        scale=math.cos(math.radians(r['lat']));best=float('inf')
        for a,b in zip(coords,coords[1:]):
            ax=(a[0]-r['lon'])*scale;ay=a[1]-r['lat'];bx=(b[0]-r['lon'])*scale;by=b[1]-r['lat']
            dx=bx-ax;dy=by-ay;t=max(0,min(1,-(ax*dx+ay*dy)/(dx*dx+dy*dy))) if dx or dy else 0
            best=min(best,math.hypot(ax+t*dx,ay+t*dy)*111195)
        if best<=150:found.append({k:r[k] for k in ('id','version','category','severity','status','updated_at')})
    return sorted(found,key=lambda r:r['id'])


class Routes:
    def __init__(self,folder):
        self.folder=Path(folder);self.lock=threading.RLock();self.graph=None;self.cache={}
        self.manifest=json.loads((self.folder/'manifest.json').read_text(encoding='utf-8'))
        self.sha=self.manifest['roads']['sha256']
    def load(self):
        if self.graph is not None:return
        import pyroutelib3 as p
        path=self.folder/self.manifest['roads']['file']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=self.sha:raise RegistryError('Road graph hash mismatch; departure disabled')
        with path.open('rb') as f:self.graph=p.osm.Graph.from_file(p.osm.CarProfile(),f,format='xml')
        self.nodes=[n for n in self.graph.nodes.values() if n.id==n.external_id and self.graph.edges.get(n.id)]
        if not self.nodes:raise RegistryError('No routable car nodes')
    def options(self,start,end,reports):
        import pyroutelib3 as p
        with self.lock:
            self.load();g=self.graph
            for c in (start,end):
                (s,w),(n,e)=self.manifest['bounds']
                if not s<=c['lat']<=n or not w<=c['lon']<=e:raise RegistryError('Camp outside saved road coverage')
            a=min(self.nodes,key=lambda x:distance(x.position,(start['lat'],start['lon'])))
            b=min(self.nodes,key=lambda x:distance(x.position,(end['lat'],end['lon'])))
            offsets=[distance(a.position,(start['lat'],start['lon'])),distance(b.position,(end['lat'],end['lon']))]
            if max(offsets)>500:raise RegistryError('No car road within 500m of camp; confirm entrance manually')
            penalised=set();results=[];seen=set()
            class Alternative:
                def get_node(self,key):return g.get_node(key)
                def get_edges(self,key):
                    return ((target,cost*(4 if (g.get_node(key).external_id,g.get_node(target).external_id) in penalised else 1)) for target,cost in g.get_edges(key))
            for attempt in range(3):
                try:path=p.find_route_without_turn_around(g if attempt==0 else Alternative(),a.id,b.id,step_limit=500000)
                except p.StepLimitExceeded:raise RegistryError('Routing search limit reached; no departure choice computed') from None
                if not path:break
                external=tuple(g.get_node(key).external_id for key in path)
                penalised.update(zip(external,external[1:]))
                if external in seen:continue
                seen.add(external);coords=[[g.get_node(key).position[1],g.get_node(key).position[0]] for key in path]
                route_id=hashlib.sha256(json.dumps([self.sha,start['id'],start['version'],end['id'],end['version'],external]).encode()).hexdigest()[:32]
                route={'route_id':route_id,'from':start['id'],'to':end['id'],'camp_versions':[start['version'],end['version']],'graph_sha256':self.sha,'geometry':{'type':'LineString','coordinates':coords},'distance_m':round(sum(distance((x[1],x[0]),(y[1],y[0])) for x,y in zip(coords,coords[1:]))),'entrance_offset_m':[round(x) for x in offsets],'warnings':nearby_reports(coords,reports)}
                self.cache[route_id]=route;results.append(route)
            if not results:raise RegistryError('No connected car route in saved graph; no straight-line replacement')
            # shortcut: bounded prototype cache, use persistent planning sessions for concurrent operators.
            if len(self.cache)>100:self.cache={r['route_id']:r for r in results}
            return {'routes':results,'source':{k:self.manifest[k] for k in ('acquired_utc','bounds','source','licence','licence_url','attribution')},'graph_sha256':self.sha,'limits':'OSM car profile, not truck clearance or current access. Alternatives penalise shared edges, not independent corridors. Camp-to-road offsets are not routed. No reports does not mean clear.'}
