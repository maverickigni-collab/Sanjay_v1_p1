"""Offline pack integrity, attribution and narrow HTTP asset exposure."""
import base64
import hashlib
import json
from pathlib import Path
import unittest

from sanjay.web import ASSETS

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'sanjay/static'


class SatelliteTests(unittest.TestCase):
    def test_offline_pack_has_real_provenance_and_matching_image(self):
        data = json.loads((STATIC / 'maps/satellite.json').read_text(encoding='utf-8'))
        image = STATIC / 'maps' / data['file']
        self.assertEqual(hashlib.sha256(image.read_bytes()).hexdigest(), data['sha256'])
        self.assertEqual(image.stat().st_size, data['bytes'])
        self.assertEqual(data['native_resolution_m'], 10)
        self.assertEqual(data['display_crs'], 'EPSG:3857')
        self.assertEqual(data['bounds'], [[22.25, 73.13], [22.4, 73.3]])
        self.assertGreaterEqual(len(data['items']), 1)
        self.assertEqual(data['aoi_quality']['nodata_percent'], 0)
        self.assertLess(data['aoi_quality']['cloud_percent'] + data['aoi_quality']['shadow_percent'], 5)
        self.assertTrue(data['terms'])
        self.assertIn('Copernicus', data['attribution'])
        self.assertTrue(image.read_bytes().startswith(b'\xff\xd8'))
        self.assertNotIn('sig=', json.dumps(data))

    def test_pinned_leaflet_assets_match_official_integrity(self):
        for name, expected in (
            ('leaflet.js', '20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo='),
            ('leaflet.css', 'p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY='),
        ):
            digest = base64.b64encode(hashlib.sha256((STATIC / 'vendor/leaflet' / name).read_bytes()).digest()).decode()
            self.assertEqual(digest, expected)
        self.assertTrue((STATIC / 'vendor/leaflet/LICENSE').is_file())

    def test_online_embed_not_an_unlicensed_tile_proxy(self):
        data = json.loads((STATIC / 'maps/online.json').read_text(encoding='utf-8'))
        self.assertTrue(data['url'].startswith('https://www.google.com/maps/embed?'))
        self.assertIn('!5e1', data['url'])
        self.assertIn('No imagery export or caching', data['limitations'])
        self.assertNotIn('/tile/', data['url'])
        self.assertNotIn('/assets/maps/esri-attribution.json', ASSETS)

    def test_account_free_street_source_and_explicit_limitations(self):
        data = json.loads((STATIC / 'maps/streets.json').read_text(encoding='utf-8'))
        self.assertEqual(data['url'], 'https://tile.openstreetmap.org/{z}/{x}/{y}.png')
        self.assertEqual(data['max_native_zoom'], 19)
        self.assertNotIn('key=', data['url'])
        self.assertIn('not satellite imagery or live road conditions', data['limitations'])
        self.assertIn('no bulk download', data['limitations'])
        self.assertIn('OpenStreetMap contributors', data['attribution'])
        self.assertIn('/assets/maps/streets.json', ASSETS)

    def test_asset_allowlist_stays_inside_static_and_excludes_research(self):
        for path, (filename, _) in ASSETS.items():
            self.assertTrue(path.startswith('/assets/'))
            self.assertTrue((STATIC / filename).resolve().is_relative_to(STATIC.resolve()))
            self.assertTrue((STATIC / filename).is_file(), filename)
        self.assertNotIn('/assets/maps/../satellite.json', ASSETS)
        self.assertNotIn('/data/map/satellite/search.json', ASSETS)


if __name__ == '__main__':
    unittest.main()
