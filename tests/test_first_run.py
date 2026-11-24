"""The general installer really serves/validates first-run setup over loopback HTTP."""
import http.client
import queue
import re
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch
from sanjay import first_run
from sanjay.operations import Operations

class FirstRunTests(unittest.TestCase):
    def test_choose_once_and_reopen_independent_identity(self):
        servers=queue.Queue();result=queue.Queue();original=first_run.ThreadingHTTPServer
        def server(*args,**kwargs):
            item=original(*args,**kwargs);servers.put(item);return item
        with patch.object(first_run,'ThreadingHTTPServer',side_effect=server):
            thread=threading.Thread(target=lambda:result.put(first_run.choose_camp(0,False)),daemon=True);thread.start()
            instance=servers.get(timeout=5);base=f'http://127.0.0.1:{instance.server_port}'
            html=urllib.request.urlopen(base,timeout=5).read().decode();csrf=re.search('name="csrf" value="([^"]+)',html)[1]
            self.assertIn('VAD-TEST-NORTH',html)
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(urllib.request.Request(base,headers={'Host':'attacker.invalid'}))
            raw=urllib.parse.urlencode({'code':'VAD-TEST-NORTH','csrf':csrf}).encode()
            response=urllib.request.urlopen(urllib.request.Request(base,data=raw,headers={'Origin':base}),timeout=5)
            self.assertEqual(response.status,200);response.read();thread.join(timeout=5);self.assertFalse(thread.is_alive())
        self.assertEqual(result.get(timeout=1),'north')
        scratch=Path(tempfile.gettempdir())
        with tempfile.TemporaryDirectory(dir=scratch) as folder:
            ops=Operations(Path(folder)/'camp',local_camp='north');code=ops.links.own();ops.close()
            ops=Operations(Path(folder)/'camp')
            try:self.assertEqual(ops.links.own(),code);self.assertEqual(list(ops.nodes),['north'])
            finally:ops.close()
