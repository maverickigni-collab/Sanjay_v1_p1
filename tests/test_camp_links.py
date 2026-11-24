"""Independent camp identities and signed profile/event exchange, not database cloning."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
import zlib

from sanjay.operations import Operations
from sanjay.camp_links import unpack,RegistryError,Conflict
from sanjay.store import encode,ExchangeError
from sanjay.first_run import CODES

SCRATCH=Path(tempfile.gettempdir())
class CampLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=SCRATCH);self.root=Path(self.tmp.name)
        self.a=Operations(self.root/'south',local_camp='south');self.b=Operations(self.root/'north',local_camp='north')
    def tearDown(self):self.a.close();self.b.close();self.tmp.cleanup()
    def pair(self):
        a=self.a.links.own();b=self.b.links.own()
        self.b.links.accept(a['code'],a['profile']['public_key']);self.a.links.accept(b['code'],b['profile']['public_key'])
    def test_code_stable_restart_and_private_keys_never_exported(self):
        a=self.a.links.own();self.assertEqual(a,self.a.links.own());self.assertEqual(set(self.a.nodes),{'south'})
        raw=encode(unpack(a['code']));self.assertNotIn(b'private.key',raw);self.assertNotIn(b'reporter_contact',raw)
        self.assertIn(b'service_area',raw);self.assertEqual(unpack(a['code'])['camp']['perimeter'],None)
        self.a.close();self.a=Operations(self.root/'south');self.assertEqual(a,self.a.links.own())
        self.assertEqual(CODES['VAD-TEST-NORTH'],'north')
    def test_pair_copies_recorded_geography_not_private_identity(self):
        oldkey=(self.b.nodes['north'].folder/'private.key').read_bytes()
        record=self.a.registry.get_camp('south')
        perimeter={'type':'Polygon','coordinates':[[[73.17,22.26],[73.18,22.26],[73.18,22.27],[73.17,22.26]]]}
        self.a.registry.update_camp('south',{'perimeter':perimeter,'name':'Recorded source camp'},record['version'])
        a=self.a.links.own()
        with self.assertRaises(RegistryError):self.b.links.accept(a['code'],'wrong')
        self.b.links.accept(a['code'],a['profile']['public_key'])
        self.assertEqual(self.b.registry.get_camp('south')['perimeter'],perimeter)
        self.assertEqual(self.b.registry.get_camp('south')['service_area'],a['profile']['camp']['service_area'])
        self.assertEqual(oldkey,(self.b.nodes['north'].folder/'private.key').read_bytes())
        self.assertFalse((self.root/'north'/'south').exists())
        self.assertFalse(self.b.links.accept(a['code'],a['profile']['public_key'])['saved'])
    def test_request_and_status_bidirectional_exchange_and_replay(self):
        self.pair();self.a.command('south','stock',{'quantity':200})
        event=self.b.command('north','request',{'quantity':100});request=event['payload']['request_id']
        self.a.links.receive(self.b.links.receive(self.a.links.packet()))
        self.assertIn(request,self.a.snapshot()['state']['requests'])
        grant=self.b.command('north','grant',{'request_id':request,'supplier':'south','quantity':100})['payload']['grant_id']
        self.a.links.receive(self.b.links.packet())
        self.a.command('south','accept',{'grant_id':grant});self.a.command('south','approve',{'grant_id':grant})
        self.b.links.receive(self.a.links.packet())
        self.assertEqual(self.b.snapshot()['state']['grants'][grant]['status'],'approved')
        before=self.b.snapshot()['events'];self.b.links.receive(self.a.links.packet());self.assertEqual(before,self.b.snapshot()['events'])
    def test_tamper_and_unpinned_packets_rejected_without_events(self):
        packet=self.a.links.packet()
        with self.assertRaises(Conflict):self.b.links.receive(packet)
        code=packet['code'];raw=json.loads(zlib.decompress(base64.urlsafe_b64decode(code[8:])))
        raw['profile']['camp']['lat']=0
        tampered='SANJAY1.'+base64.urlsafe_b64encode(zlib.compress(encode(raw))).decode()
        with self.assertRaises(RegistryError):unpack(tampered)
        with self.assertRaises(RegistryError):unpack('SANJAY1.'+base64.urlsafe_b64encode(zlib.compress(b'x'*70000)).decode())
        self.assertEqual(self.b.snapshot()['events'],[])
    def test_old_profile_revision_and_same_origin_key_replacement_rejected(self):
        self.pair();old=self.a.links.own()
        record=self.a.registry.get_camp('south');self.a.registry.update_camp('south',{'name':'New revision'},record['version'])
        new=self.a.links.own();self.b.links.accept(new['code'],new['profile']['public_key'])
        with self.assertRaises(Conflict):self.b.links.accept(old['code'],old['profile']['public_key'])
        other=Operations(self.root/'other',local_camp='south')
        try:
            code=other.links.own()
            with self.assertRaises(Conflict):self.b.links.accept(code['code'],code['profile']['public_key'])
        finally:other.close()
        with self.assertRaises(Conflict):self.a.links.accept(old['code'],old['profile']['public_key'])
    def test_valid_signature_cannot_relabel_a_pinned_key_or_author_untrusted_origin(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from sanjay.camp_links import CONTEXT
        from sanjay.store import _b64
        self.pair();key=Ed25519PrivateKey.from_private_bytes((self.a.nodes['south'].folder/'private.key').read_bytes())
        code=self.a.links.own()['code'];envelope=json.loads(zlib.decompress(base64.urlsafe_b64decode(code[8:])))
        envelope['profile']['node']='east';envelope['profile']['camp']['id']='east'
        envelope['signature']=_b64(key.sign(CONTEXT+encode(envelope['profile'])))
        relabelled='SANJAY1.'+base64.urlsafe_b64encode(zlib.compress(encode(envelope))).decode()
        self.assertEqual(unpack(relabelled)['node'],'east')
        with self.assertRaises(Conflict):self.b.links.accept(relabelled,envelope['profile']['public_key'])
        event=self.a.command('south','stock',{'quantity':1});event['origin']='east';raw=encode(event)
        packet=self.a.links.packet();packet['bundle']=_b64(encode({'version':1,'events':[{'data':_b64(raw),'signature':_b64(key.sign(raw))}]}))
        with self.assertRaises(ExchangeError):self.b.links.receive(packet)
        self.assertEqual(self.b.snapshot()['events'],[])
    def test_remote_actor_cannot_be_impersonated(self):
        with self.assertRaises(RegistryError):self.a.command('north','stock',{'quantity':200})
        self.assertEqual(self.a.snapshot()['state']['stocks'],{})

if __name__=='__main__':unittest.main()
