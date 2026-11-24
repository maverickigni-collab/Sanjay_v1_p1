"""Exercise actual loopback HTTP servers and signed file-transfer endpoints."""
import http.cookiejar
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from sanjay.store import Store
from sanjay.web import make_server


class Browser:
    def __init__(self, store):
        self.server, self.url = make_server(store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = self.url.split('/?')[0]
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        html = self.opener.open(self.url).read().decode()
        self.csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def post(self, kind, **fields):
        raw = urllib.parse.urlencode({'csrf': self.csrf, 'kind': kind, **fields}).encode()
        with self.opener.open(self.base + '/command', data=raw) as response:
            return response.read()

    def state(self):
        with self.opener.open(self.base + '/state') as response:
            return json.load(response)

    def export(self):
        with self.opener.open(self.base + '/export') as response:
            return response.read()

    def receive(self, raw):
        request = urllib.request.Request(self.base + '/import', data=raw, headers={'X-CSRF-Token': self.csrf, 'Content-Type': 'application/json'})
        with self.opener.open(request) as response:
            return json.load(response)


class WebTests(unittest.TestCase):
    def setUp(self):
        scratch = Path.home() / 'AppData/Local/hermes/cache/scratch'
        scratch.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.tmp.name)
        a = Store.create(self.root / 'A', 'A')
        b = Store.create(self.root / 'B', 'B')
        a.trust((b.folder / 'public.json').read_bytes())
        b.trust((a.folder / 'public.json').read_bytes())
        self.a, self.b = Browser(a), Browser(b)

    def tearDown(self):
        self.a.close(); self.b.close(); self.tmp.cleanup()

    def test_complete_http_workflow_signed_download_upload(self):
        self.a.post('stock', quantity=200)
        self.b.post('request', quantity=100)
        request = next(iter(self.b.state()['state']['requests']))
        self.b.post('grant', request_id=request, supplier='A', quantity=100)
        grant = next(iter(self.b.state()['state']['grants']))
        self.a.receive(self.b.export())
        with self.a.opener.open(self.a.base) as response:
            self.assertIn('Accept & reserve supplies', response.read().decode())
        self.a.post('accept', grant_id=grant)
        with self.a.opener.open(self.a.base) as response:
            self.assertIn('Review dispatch approval', response.read().decode())
        self.a.post('approve', grant_id=grant)
        with self.a.opener.open(self.a.base) as response:
            self.assertIn('Review approved departure', response.read().decode())
        self.a.post('dispatch', grant_id=grant)
        dispatch = next(iter(self.a.state()['state']['dispatches']))
        self.b.receive(self.a.export())
        self.b.post('receipt', dispatch_id=dispatch, quantity=90)
        self.a.receive(self.b.export())
        self.a.receive(self.b.export())
        state = self.a.state()['state']
        self.assertEqual(state['stocks']['A']['on_hand'], 100)
        self.assertEqual(state['stocks']['B']['on_hand'], 90)
        self.assertEqual(state['requests'][request]['quantity'] - state['requests'][request]['received'], 10)
        self.assertEqual(state['dispatches'][dispatch]['quantity'] - state['dispatches'][dispatch]['received'], 10)
        with self.a.opener.open(self.a.base + '/?view=dispatch') as response:
            page = response.read().decode()
        self.assertIn('Unconfirmed Difference', page)
        self.assertIn('Fictional prototype', page)
        with self.b.opener.open(self.b.base) as response:
            self.assertIn('Set initial stock once', response.read().decode())
        self.b.post('stock', quantity=10)
        self.assertEqual(self.b.state()['state']['stocks']['B']['on_hand'], 100)
        with self.b.opener.open(self.b.base) as response:
            self.assertNotIn('Set initial stock once', response.read().decode())

    def test_ledger_views_local_assets_and_safe_return_view(self):
        for view in ('map', 'reports', 'requests', 'commitments', 'dispatch', 'exchange', 'receipts', 'history'):
            with self.a.opener.open(self.a.base + '/?view=' + view) as response:
                page = response.read().decode()
                self.assertIn('data-view="' + view + '"', page)
                self.assertEqual(response.headers['Referrer-Policy'], 'same-origin')
                self.assertIn("style-src 'self'", response.headers['Content-Security-Policy'])
        for asset, mime in (('app.css', 'text/css'), ('app.js', 'text/javascript')):
            with self.a.opener.open(self.a.base + '/assets/' + asset) as response:
                self.assertTrue(response.headers['Content-Type'].startswith(mime))
                self.assertGreater(len(response.read()), 100)
        before = self.a.state()
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.a.post('request', quantity=100, return_view='https://attacker.invalid')
        self.assertEqual(raised.exception.code, 400)
        self.assertEqual(before, self.a.state())
        with self.a.opener.open(self.a.base + '/?message=%3Cscript%3Ealert(1)%3C/script%3E') as response:
            page = response.read().decode()
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertNotIn('<script>alert(1)</script>', page)

    def test_map_default_reports_and_safe_map_return(self):
        with self.a.opener.open(self.a.base) as response:
            self.assertIn('data-view="map"', response.read().decode())
        self.a.post('stock', quantity=200, return_view='map')
        with self.a.opener.open(self.a.base + '/?view=reports') as response:
            page = response.read().decode()
        self.assertIn('Phones cannot submit reports yet', page)
        self.assertEqual(self.a.state()['state']['stocks']['A']['on_hand'], 200)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.a.opener.open(self.a.base + '/assets/../../store.py')
        self.assertEqual(raised.exception.code, 404)

    def test_hybrid_map_assets_and_csp(self):
        with self.a.opener.open(self.a.base) as response:
            page = response.read().decode()
            csp = response.headers['Content-Security-Policy']
        self.assertIn('frame-src https://www.google.com', csp)
        self.assertIn("img-src 'self' data:", csp)
        self.assertIn("img-src 'self' data: https://tile.openstreetmap.org;", csp)
        self.assertIn("connect-src 'self';", csp)
        self.assertNotIn('img-src *', csp)
        self.assertIn('/assets/leaflet/leaflet.js', page)
        self.assertIn('/assets/satellite.js', page)
        for asset, mime in (
            ('maps/satellite.json', 'application/json'),
            ('maps/online.json', 'application/json'),
            ('maps/four-camps.json', 'application/json'),
            ('maps/streets.json', 'application/json'),
            ('maps/vadodara-sentinel.jpg', 'image/jpeg'),
            ('leaflet/leaflet.js', 'text/javascript'),
            ('leaflet/leaflet.css', 'text/css'),
            ('satellite.js', 'text/javascript'),
        ):
            url = self.a.base + '/assets/' + asset
            with self.a.opener.open(url) as response:
                self.assertTrue(response.headers['Content-Type'].startswith(mime))
                self.assertGreater(len(response.read()), 100)
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(url)
            self.assertEqual(raised.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.a.opener.open(self.a.base + '/assets/maps/../../store.py')
        self.assertEqual(raised.exception.code, 404)

    def test_four_map_camps_do_not_seed_or_change_signed_supply_records(self):
        before = self.a.state()
        with self.a.opener.open(self.a.base + '/assets/maps/four-camps.json') as response:
            fixture = json.load(response)
        self.assertEqual(len(fixture['camps']), 4)
        self.assertEqual(len(fixture['zones']['features']), 4)
        self.assertTrue(all(c['ledger_origin'] is None and c['stock'] is None for c in fixture['camps']))
        with self.a.opener.open(self.a.base + '/?view=map') as response:
            self.assertEqual(response.read().decode().count('data-select-camp='), 4)
        self.assertEqual(before, self.a.state())

    def test_unauthorised_csrf_origin_and_invalid_form(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(self.a.base + '/state')
        self.assertEqual(raised.exception.code, 403)
        before = self.a.state()
        bad = urllib.request.Request(self.a.base + '/import', data=b'{}')
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.a.opener.open(bad)
        self.assertEqual(raised.exception.code, 403)
        bad = urllib.request.Request(self.a.base + '/import', data=b'{}', headers={'X-CSRF-Token': self.a.csrf, 'Origin': 'https://attacker.invalid'})
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.a.opener.open(bad)
        self.assertEqual(raised.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.a.post('stock', quantity=-200)
        self.assertEqual(raised.exception.code, 400)
        self.assertEqual(before, self.a.state())


if __name__ == '__main__':
    unittest.main()
