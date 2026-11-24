"""Generated offline detail retains every saved way without changing the routing source."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
class RoadDetailTests(unittest.TestCase):
    def test_saved_vector_detail_matches_acquired_graph(self):
        manifest=json.loads((ROOT/'data/map/operations/manifest.json').read_text(encoding='utf-8'))
        data=json.loads((ROOT/'sanjay/static/maps/road-detail.json').read_text(encoding='utf-8'))
        self.assertGreater(len(data['buildings']['geometry']['coordinates']),0)
        self.assertTrue(all(len(ring)>=4 and ring[0]==ring[-1] for polygon in data['buildings']['geometry']['coordinates'] for ring in polygon))
        self.assertTrue(any(label['kind']=='landmark' for label in data['labels']))
        self.assertTrue(any(label['kind']=='road' for label in data['labels']))
        self.assertEqual(len(data['building_source']['areas']),4)
        self.assertNotIn('reporter_contact',json.dumps(data))
        self.assertEqual(data['source_sha256'],manifest['roads']['sha256'])
        self.assertEqual(data['source']['bounds'],manifest['bounds'])
        self.assertEqual(data['source']['acquired_utc'],manifest['acquired_utc'])
        self.assertEqual(data['source']['licence'],'ODbL 1.0')
        self.assertEqual([f['properties']['class'] for f in data['features']],['major','local'])
        lines=[line for feature in data['features'] for line in feature['geometry']['coordinates']]
        self.assertEqual(len(lines),manifest['roads']['ways'])
        self.assertEqual(data['way_count'],len(lines))
        self.assertTrue(all(len(line)>=2 for line in lines))
        self.assertTrue(all(-180<=lon<=180 and -90<=lat<=90 for line in lines for lon,lat in line))
        source=ROOT/'data/map/operations'/manifest['roads']['file']
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),data['source_sha256'])
