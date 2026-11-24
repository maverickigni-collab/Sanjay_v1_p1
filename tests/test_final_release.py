"""Final release checks use real child pipes, HTTP and artifact hashes."""
import hashlib
import http.server
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

from sanjay.camp_links import ssh_exchange,RegistryError
from sanjay.operations import Operations
from sanjay.ops_web import make_services
from sanjay.store import MAX_EVENTS,ExchangeError

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_launcher',ROOT/'start.py');launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
spec=importlib.util.spec_from_file_location('release_builder',ROOT/'tools/build_release.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class SSHStreamTests(unittest.TestCase):
    def test_valid_response_and_remote_trust_diagnostic(self):
        output=ssh_exchange([sys.executable,'-c',"import sys;sys.stdin.buffer.read();print('{}')"],b'{}',timeout=3)
        self.assertEqual(json.loads(output),{})
        with self.assertRaisesRegex(RegistryError,'Remote camp: .*profile changed'):
            ssh_exchange([sys.executable,'-c',"import sys;sys.stdin.buffer.read();print('{\"error\":\"profile changed; accept its new code\"}');sys.exit(1)"],b'{}',timeout=3)
    def test_blocked_write_is_timed_out(self):
        before=time.monotonic()
        with self.assertRaisesRegex(RegistryError,'timed out'):
            ssh_exchange([sys.executable,'-c','import time;time.sleep(30)'],b'x'*900000,timeout=.4)
        self.assertLess(time.monotonic()-before,8)
    def test_oversized_output_is_not_success(self):
        with self.assertRaisesRegex(RegistryError,'exceeds 1 MB'):
            ssh_exchange([sys.executable,'-c',"import sys;sys.stdin.buffer.read();sys.stdout.buffer.write(b'x'*1100000)"],b'{}',timeout=3)
    def test_truncated_peer_output_is_rejected_by_parser(self):
        from sanjay.store import parse_json
        output=ssh_exchange([sys.executable,'-c',"import sys;sys.stdin.buffer.read();print('{')"],b'{}',timeout=3)
        with self.assertRaises(ExchangeError):parse_json(output)

class ReleaseTests(unittest.TestCase):
    def test_pristine_export_integrity_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'clean';result=builder.build(out)
            self.assertEqual(launcher.check_release(out)['mode'],'verified release')
            self.assertEqual(result['files'],len([p for p in out.rglob('*') if p.is_file()]))
            self.assertFalse((out/'camp-data').exists());self.assertFalse((out/'.ssh').exists())
            self.assertFalse(any(p.name.startswith('phone-camp-') or '.sqlite' in p.name or p.name in ('private.key','camp-wifi.json','transport_ed25519') for p in out.rglob('*')))
            (out/'camp-data').mkdir();(out/'camp-data/private.key').write_bytes(b'test-only-generated-fixture')
            self.assertTrue(launcher.check_release(out)['ok'],'Runtime private data is outside the public manifest')
            asset=out/'sanjay/static/ops.js';asset.write_bytes(asset.read_bytes()+b'\n// changed fixture')
            with self.assertRaisesRegex(ValueError,'changed'):launcher.check_release(out)
            with self.assertRaises(FileExistsError):builder.build(out)
    def test_manifest_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('main.py','start.py','requirements.txt','sanjay/operations.py','sanjay/camp_links.py','sanjay/static/maps/road-detail.json','data/map/operations/manifest.json','data/map/operations/vadodara-roads.osm','data/map/operations/facilities.json'):
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture')
            (root/'RELEASE_MANIFEST.json').write_text(json.dumps({'format':1,'files':{'../outside':'0'*64}}))
            with self.assertRaisesRegex(ValueError,'Invalid manifest'):launcher.check_release(root)

class FinalHTTPTests(unittest.TestCase):
    def test_remote_inspection_keeps_actor_and_bridge_error_is_structured(self):
        with tempfile.TemporaryDirectory() as temp:
            ops=Operations(Path(temp)/'camp',local_camp='north');servers,url=make_services(ops)
            for server in servers:threading.Thread(target=server.serve_forever,daemon=True).start()
            try:
                html=urllib.request.urlopen(url).read().decode()
                self.assertIn('camp=north',html);self.assertNotIn('view=facilities&amp;camp=south',html)
                self.assertNotIn('Operate south supplies',html)
                with patch.object(launcher.webbrowser,'open') as browser:
                    self.assertTrue(launcher.reopen_existing(['--root',str(ops.registry.folder),'--port',str(servers[0].server_port)]))
                    browser.assert_called_once()
                with patch.object(launcher.webbrowser,'open') as browser:
                    self.assertFalse(launcher.reopen_existing(['--root',str(Path(temp)/'different'),'--port',str(servers[0].server_port)]))
                    browser.assert_not_called()
                csrf=re.search('name="csrf-token" content="([^"]+)',html)[1]
                request=urllib.request.Request(url+'api/camp',data=json.dumps({'id':'south','changes':{'name':'FORBIDDEN'},'version':1}).encode(),headers={'Content-Type':'application/json','Origin':url.rstrip('/'),'X-CSRF-Token':csrf})
                with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
                self.assertEqual(error.exception.code,400)
                self.assertNotEqual(ops.registry.get_camp('south')['name'],'FORBIDDEN')
                output=subprocess.run([sys.executable,str(ROOT/'tools/ssh_bridge.py'),'--port',str(servers[0].server_port)],input=b'{"bad":"packet"}',capture_output=True,timeout=10)
                self.assertEqual(output.returncode,1);self.assertIn('error',json.loads(output.stdout));self.assertNotIn(b'Traceback',output.stdout)
            finally:
                for server in servers:server.shutdown();server.server_close()
                ops.close()
    def test_real_500_event_limit_is_visible_not_truncated(self):
        self.assertEqual(MAX_EVENTS,500)
        with tempfile.TemporaryDirectory() as temp:
            ops=Operations(Path(temp)/'camp',local_camp='south')
            try:
                # Batch-import 500 genuinely signed fixtures; avoid quadratic per-command rebuilds.
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                from sanjay.store import encode,_b64
                from sanjay.registry import now
                import uuid
                node=ops.nodes['south'];key=Ed25519PrivateKey.from_private_bytes((node.folder/'private.key').read_bytes());events=[]
                for sequence in range(1,501):
                    event={'v':1,'id':str(uuid.uuid4()),'origin':'south','seq':sequence,'at':now(),'kind':'request','payload':{'request_id':str(uuid.uuid4()),'quantity':1}}
                    raw=encode(event);events.append({'data':_b64(raw),'signature':_b64(key.sign(raw))})
                node.import_bundle(encode({'version':1,'events':events}))
                self.assertEqual(len(ops.snapshot()['events']),500)
                with self.assertRaises(ExchangeError):ops.command('south','request',{'quantity':1})
                self.assertEqual(len(ops.snapshot()['events']),500)
                self.assertTrue(ops.links.packet()['bundle'])
            finally:ops.close()

if __name__=='__main__':unittest.main()
