"""Actual four-node persistence, real road graph, HTTP intake and isolation."""
import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from urllib.parse import urlsplit,urlencode
import urllib.request
import http.cookiejar
import io

from sanjay.operations import Operations
from sanjay.ops_web import make_services
from sanjay.registry import RegistryError,Conflict
from sanjay.routing import nearby_reports

SCRATCH=Path.home()/'AppData/Local/hermes/cache/scratch'

class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=SCRATCH);self.root=Path(self.tmp.name)/'ops';self.ops=Operations(self.root)
    def tearDown(self):self.ops.close();self.tmp.cleanup()
    def approved(self):
        self.ops.command('south','stock',{'quantity':200})
        request=self.ops.command('north','request',{'quantity':100})['payload']['request_id']
        grant=self.ops.command('north','grant',{'request_id':request,'supplier':'south','quantity':100})['payload']['grant_id']
        self.ops.command('south','accept',{'grant_id':grant});self.ops.command('south','approve',{'grant_id':grant})
        return request,grant
    def test_real_data_loaded_and_independent_keys_without_stock(self):
        self.assertFalse(self.ops.data_error,self.ops.data_error)
        s=self.ops.snapshot();self.assertEqual(len(s['registry']['camps']),4);self.assertGreaterEqual(len(s['registry']['facilities']),488)
        self.assertEqual(s['state']['stocks'],{})
        self.assertEqual(len({(n.folder/'private.key').read_bytes() for n in self.ops.nodes.values()}),4)
        with self.assertRaises(RegistryError):Operations(self.root)
    def test_missing_provisioned_node_is_not_recreated(self):
        self.ops.close();original=self.root/'north';moved=self.root/'north-test-held'
        original.rename(moved)
        try:
            with self.assertRaisesRegex(RegistryError,'Provisioned node missing'):Operations(self.root)
            self.assertFalse(original.exists())
        finally:moved.rename(original)
        self.ops=Operations(self.root)
    def test_facilities_search_inside_and_nearby(self):
        found=self.ops.facilities('south','Billabong');self.assertTrue(found)
        rows=self.ops.facilities('south');self.assertTrue(any(f['inside_service_area'] for f in rows));self.assertTrue(any(not f['inside_service_area'] for f in rows))
        self.assertTrue(all(f['straight_line_m']>=0 for f in rows))
    def test_four_node_signed_scenario_route_gate_restart_replay(self):
        request,grant=self.approved()
        before=self.ops.nodes['south'].snapshot()
        with self.assertRaises(RegistryError):self.ops.command('south','dispatch',{'grant_id':grant})
        self.assertEqual(before,self.ops.nodes['south'].snapshot())
        routes=self.ops.routes('south',grant)['routes'];self.assertGreaterEqual(len(routes),2)
        self.assertTrue(all(len(r['geometry']['coordinates'])>2 and r['distance_m']>0 for r in routes))
        self.ops.choose_route('south',grant,routes[0]['route_id'],True)
        dispatch=self.ops.command('south','dispatch',{'grant_id':grant})['payload']['dispatch_id']
        self.ops.command('north','receipt',{'dispatch_id':dispatch,'quantity':90})
        snap=self.ops.nodes['south'].snapshot();state=snap['state']
        self.assertEqual(state['stocks']['south']['on_hand'],100);self.assertEqual(state['stocks']['north']['on_hand'],90)
        self.assertEqual(state['requests'][request]['quantity']-state['requests'][request]['received'],10)
        self.assertEqual(state['dispatches'][dispatch]['quantity']-state['dispatches'][dispatch]['received'],10)
        self.ops.sync();self.assertEqual(snap,self.ops.nodes['south'].snapshot())
        self.ops.close();self.ops=Operations(self.root);self.assertEqual(snap,self.ops.nodes['south'].snapshot())
        self.assertTrue(self.ops.registry.get_route_choice(grant))
    def test_relevant_report_changes_invalidate_route_and_ack_required(self):
        _,grant=self.approved();r=self.ops.routes('south',grant)['routes'][0]
        self.ops.choose_route('south',grant,r['route_id'],True)
        lon,lat=r['geometry']['coordinates'][len(r['geometry']['coordinates'])//2]
        report=self.ops.registry.submit_report({'category':'road_obstruction','description':'Fictional test only','lat':lat,'lon':lon,'severity':'high','reporter_contact':'PRIVATE'})
        with self.assertRaises(Conflict):self.ops.command('south','dispatch',{'grant_id':grant})
        fresh=self.ops.routes('south',grant)['routes'];affected=next(route for route in fresh if any(w['id']==report['id'] for w in route['warnings']))
        with self.assertRaises(RegistryError):self.ops.choose_route('south',grant,affected['route_id'],False)
        self.ops.choose_route('south',grant,affected['route_id'],True)
        self.ops.registry.update_camp('south',{'name':'Changed fixture'},1)
        with self.assertRaises(Conflict):self.ops.command('south','dispatch',{'grant_id':grant})
    def test_engine_oneway_private_access_and_turn_restriction(self):
        import pyroutelib3 as p
        xml=b'''<osm version="0.6"><node id="1" lat="0" lon="0"/><node id="2" lat="0" lon="0.001"/><node id="3" lat="0.001" lon="0.001"/><node id="4" lat="0.001" lon="0"/><way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/><tag k="oneway" v="yes"/></way><way id="20"><nd ref="2"/><nd ref="3"/><tag k="highway" v="residential"/></way><way id="30"><nd ref="2"/><nd ref="4"/><nd ref="3"/><tag k="highway" v="residential"/></way><way id="40"><nd ref="1"/><nd ref="3"/><tag k="highway" v="residential"/><tag k="access" v="private"/></way><relation id="50"><member type="way" ref="10" role="from"/><member type="node" ref="2" role="via"/><member type="way" ref="20" role="to"/><tag k="type" v="restriction"/><tag k="restriction" v="no_right_turn"/></relation></osm>'''
        g=p.osm.Graph.from_file(p.osm.CarProfile(),io.BytesIO(xml),format='xml')
        route=p.find_route_without_turn_around(g,1,3);self.assertEqual([g.get_node(n).external_id for n in route],[1,2,4,3])
        self.assertEqual(p.find_route_without_turn_around(g,3,1),[])

class OperationsHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=SCRATCH);self.ops=Operations(Path(self.tmp.name)/'ops')
        self.servers,self.url=make_services(self.ops)
        for s in self.servers:threading.Thread(target=s.serve_forever,daemon=True).start()
        self.operator=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        html=self.operator.open(self.url).read().decode();self.csrf=re.search('name="csrf-token" content="([^"]+)',html)[1]
        self.base=f'http://127.0.0.1:{self.servers[0].server_port}'
        self.invite=json.load(self.operator.open(self.base+'/api/invite'))
        self.phone=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        html=self.phone.open(self.invite['url']).read().decode();self.phone_csrf=re.search('name="csrf-token" content="([^"]+)',html)[1];self.ticket=re.search('name="form-ticket" content="([^"]+)',html)[1]
        self.phone_base=f'http://127.0.0.1:{self.servers[1].server_port}'
    def tearDown(self):
        for s in self.servers:s.shutdown();s.server_close()
        self.ops.close();self.tmp.cleanup()
    def post(self,opener,url,fields,csrf,ticket=None):
        headers={'Content-Type':'application/json','Origin':urlsplit(url).scheme+'://'+urlsplit(url).netloc,'X-CSRF-Token':csrf}
        if ticket:headers['X-Form-Ticket']=ticket
        return opener.open(urllib.request.Request(url,data=json.dumps(fields).encode(),headers=headers))
    def test_operator_requires_no_private_token_but_preserves_request_guards(self):
        self.assertNotIn('token=',self.url)
        fresh=urllib.request.build_opener()
        response=fresh.open(self.base+'/');html=response.read().decode()
        self.assertEqual(response.status,200);self.assertIn('Acting camp',html)
        self.assertIsNone(response.headers.get('Set-Cookie'))
        self.assertEqual(fresh.open(self.base+'/state').status,200)
        for headers in ({'Host':'attacker.invalid'},{'Sec-Fetch-Site':'cross-site'}):
            with self.assertRaises(urllib.error.HTTPError) as error:fresh.open(urllib.request.Request(self.base+'/',headers=headers))
            self.assertEqual(error.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as error:self.post(fresh,self.base+'/api/invite/rotate',{},'wrong')
        self.assertEqual(error.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            fresh.open(urllib.request.Request(self.base+'/api/invite/rotate',data=b'{}',headers={'Content-Type':'application/json','Origin':'http://attacker.invalid','X-CSRF-Token':self.csrf}))
        self.assertEqual(error.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as error:fresh.open(self.phone_base+'/')
        self.assertEqual(error.exception.code,403)
    def test_react_interaction_assets_are_local_and_operator_only(self):
        html=self.operator.open(self.base+'/?view=reports').read().decode()
        self.assertIn('id="branched-menu-root"',html)
        self.assertIn('id="workspace-navigation"',html)
        self.assertIn('<summary>Workspace navigation</summary>',html)
        for asset in ('interactions.js','interactions.css'):
            self.assertIn('/assets/'+asset,html)
            self.assertEqual(self.operator.open(self.base+'/assets/'+asset).status,200)
            with self.assertRaises(urllib.error.HTTPError) as error:self.phone.open(self.phone_base+'/assets/'+asset)
            self.assertEqual(error.exception.code,404)
        self.assertIn('nonce="'+self.csrf+'" src="/assets/interactions.js"',html)
    def test_fresh_qr_serves_form_without_cross_site_redirect(self):
        url=urlsplit(self.invite['url']);connection=http.client.HTTPConnection(url.hostname,url.port,timeout=5)
        connection.request('GET',url.path+'?'+url.query,headers={'Sec-Fetch-Site':'cross-site'})
        response=connection.getresponse();body=response.read()
        self.assertEqual(response.status,200);self.assertIsNone(response.getheader('Location'))
        self.assertIn('SameSite=Strict',response.getheader('Set-Cookie'));self.assertIn('HttpOnly',response.getheader('Set-Cookie'))
        self.assertIn(b'id="phone-form"',body);self.assertIn(b'form-ticket',body);connection.close()
    def test_phone_submit_private_review_resolution_and_replay(self):
        fields={'category':'road_obstruction','description':'<img src=x onerror=alert(1)> FICTIONAL','lat':22.3,'lon':73.2,'severity':'critical','reporter_contact':'PRIVATE-TEST-PHONE'}
        report=json.load(self.post(self.phone,self.phone_base+'/api/reports',fields,self.phone_csrf,self.ticket))
        self.assertEqual(report['status'],'unverified')
        with self.assertRaises(urllib.error.HTTPError) as error:self.post(self.phone,self.phone_base+'/api/reports',fields,self.phone_csrf,self.ticket)
        self.assertEqual(error.exception.code,403)
        snapshot=self.operator.open(self.base+'/state').read();self.assertNotIn(b'PRIVATE-TEST-PHONE',snapshot)
        detail=json.load(self.operator.open(self.base+'/api/report?id='+report['id']));self.assertEqual(detail['reporter_contact'],'PRIVATE-TEST-PHONE')
        for status,version in [('confirmed',1),('resolved',2)]:
            r=json.load(self.post(self.operator,self.base+'/api/review',{'camp':'south','id':report['id'],'status':status,'version':version},self.csrf));self.assertEqual(r['status'],status);self.assertNotIn('reporter_contact',r)
        for path in ('/state','/api/report?id='+report['id'],'/export','/api/invite','/assets/maps/four-camps.json'):
            with self.assertRaises(urllib.error.HTTPError) as error:self.phone.open(self.phone_base+path)
            self.assertEqual(error.exception.code,404)
        self.assertEqual(self.ops.nodes['south'].snapshot()['state']['stocks'],{})
    def test_host_csrf_origin_body_rate_expiry_revocation_and_qr(self):
        qr=self.operator.open(self.base+'/api/invite.png');self.assertEqual(qr.headers['Content-Type'],'image/svg+xml');self.assertIn(b'<svg',qr.read())
        body={'category':'other','description':'fixture','lat':22.3,'lon':73.2,'severity':'unknown','reporter_contact':''}
        for headers,expected in [({'Host':'attacker.invalid'},403),({'Origin':'http://attacker.invalid'},403),({'X-CSRF-Token':'wrong'},403),({'Content-Length':'20000'},413)]:
            connection=http.client.HTTPConnection('127.0.0.1',self.servers[1].server_port,timeout=5)
            cookie=f'sanjay_{self.servers[1].server_port}={self.servers[1].invitation.token}'
            h={'Cookie':cookie,'Origin':self.phone_base,'Content-Type':'application/json','X-CSRF-Token':self.phone_csrf,'X-Form-Ticket':self.ticket,**headers}
            # Early header rejections must not race a second body write on Windows TCP.
            payload=json.dumps(body) if 'X-CSRF-Token' in headers else None
            connection.request('POST','/api/reports',payload,h);response=connection.getresponse();self.assertEqual(response.status,expected);response.read();connection.close()
        invite=self.servers[1].invitation;invite.hits.clear()
        for _ in range(10):
            html=self.phone.open(self.phone_base).read().decode();ticket=re.search('name="form-ticket" content="([^"]+)',html)[1]
            self.post(self.phone,self.phone_base+'/api/reports',body,self.phone_csrf,ticket).read()
        with self.assertRaises(urllib.error.HTTPError) as error:self.post(self.phone,self.phone_base+'/api/reports',body,self.phone_csrf,self.ticket)
        self.assertEqual(error.exception.code,429)
        self.post(self.operator,self.base+'/api/invite/rotate',{},self.csrf).read()
        with self.assertRaises(urllib.error.HTTPError) as error:self.phone.open(self.phone_base)
        self.assertEqual(error.exception.code,403)
        invite.deadline=0
        with self.assertRaises(urllib.error.HTTPError) as error:self.phone.open(self.phone_base+'/?invite='+invite.token)
        self.assertEqual(error.exception.code,403)
    def test_phone_invitation_is_rechecked_at_commit(self):
        from unittest.mock import patch
        fields={'category':'other','description':'fixture','lat':22.3,'lon':73.2,'severity':'unknown','reporter_contact':''}
        with patch.object(self.servers[1].RequestHandlerClass,'authorised',side_effect=[True,False]):
            with self.assertRaises(urllib.error.HTTPError) as error:self.post(self.phone,self.phone_base+'/api/reports',fields,self.phone_csrf,self.ticket)
            self.assertEqual(error.exception.code,403)
        self.assertEqual(self.ops.registry.snapshot()['reports'],[])
    def test_all_operator_views_and_db_backed_geometry(self):
        for view in ('map','requests','commitments','dispatch','receipts','exchange','history','facilities','reports','routes','phone'):
            self.assertEqual(self.operator.open(self.base+'/?view='+view+'&camp=north').status,200)
        shape={'type':'Polygon','coordinates':[[[73.17,22.26],[73.18,22.26],[73.18,22.27],[73.17,22.26]]]}
        self.post(self.operator,self.base+'/api/camp',{'id':'south','changes':{'perimeter':shape},'version':1},self.csrf).read()
        fixture=json.load(self.operator.open(self.base+'/assets/maps/four-camps.json'))
        self.assertEqual(next(c for c in fixture['camps'] if c['id']=='south')['perimeter'],shape)

if __name__=='__main__':unittest.main()
