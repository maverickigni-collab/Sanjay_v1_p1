"""Four test sites, exact demo-area partition, and isolation from signed stock."""
from collections import Counter
from copy import deepcopy
import math
import unittest
from unittest.mock import patch

from sanjay.geography import DIRECTIONS, load_test_geography, point_inside, ring_area, validate_test_geography
from sanjay.ui import render
from sanjay.workflow import initial_state


class TestCampGeography(unittest.TestCase):
    def setUp(self):
        self.data = load_test_geography()

    def test_four_camps_four_zones_and_requested_south_school(self):
        self.assertEqual(len(self.data['camps']), 4)
        self.assertEqual(len(self.data['zones']['features']), 4)
        self.assertEqual({c['id'] for c in self.data['camps']}, set(DIRECTIONS))
        south = next(c for c in self.data['camps'] if c['id'] == 'south')
        self.assertIn('Billabong High International School', south['host_name'])
        self.assertEqual(south['coordinates'], [73.173314, 22.262142])
        self.assertIn('Private school', south['host_type'])
        self.assertTrue(self.data['test_only'])
        self.assertIn('NOT official', self.data['scope_label'])

    def test_exact_rectangle_coverage_and_shared_edges(self):
        (south, west), (north, east) = self.data['scope_bounds']
        rings = [f['geometry']['coordinates'][0] for f in self.data['zones']['features']]
        area = sum(ring_area(r) for r in rings)
        self.assertTrue(math.isclose(area, (east-west)*(north-south), rel_tol=1e-9))
        edges = Counter(tuple(sorted((tuple(a), tuple(b)))) for ring in rings for a,b in zip(ring,ring[1:]))
        self.assertEqual(Counter(edges.values()), {1: 4, 2: 4})
        for feature in self.data['zones']['features']:
            self.assertEqual(feature['geometry']['coordinates'][0][0], feature['geometry']['coordinates'][0][-1])

    def test_each_host_is_strictly_inside_only_its_own_zone(self):
        zones = self.data['zones']['features']
        for camp in self.data['camps']:
            matches = [f['properties']['id'] for f in zones if point_inside(camp['coordinates'], f['geometry']['coordinates'][0])]
            self.assertEqual(matches, [camp['zone_id']])
            for field in ('stock', 'capacity', 'physical_perimeter', 'ledger_origin'):
                self.assertIsNone(camp[field])
            self.assertEqual(camp['permission_status'], 'not_requested')
            self.assertEqual(camp['coordinate_status'], 'provisional')

    def test_rejects_missing_duplicate_and_misplaced_camps(self):
        cases=[]
        item=deepcopy(self.data); item['camps'].pop(); cases.append(item)
        item=deepcopy(self.data); item['camps'][1]['id']='south'; cases.append(item)
        item=deepcopy(self.data); item['camps'][0]['coordinates']=[73.19,22.38]; cases.append(item)
        item=deepcopy(self.data); item['camps'][0]['coordinates']=[True,22.26]; cases.append(item)
        item=deepcopy(self.data); item['schema_version']=True; cases.append(item)
        for value in cases:
            with self.subTest(value=value.get('schema_version')):
                with self.assertRaises(ValueError): validate_test_geography(value)

    def test_rejects_overlaps_gaps_and_invented_authority(self):
        for field, value in [('stock',0),('capacity',200),('ledger_origin','A'),('physical_perimeter',[[0,0],[1,1]]),('permission_status','approved')]:
            item=deepcopy(self.data); item['camps'][0][field]=value
            with self.subTest(field=field):
                with self.assertRaises(ValueError): validate_test_geography(item)
        item=deepcopy(self.data)
        item['zones']['features'][0]['geometry']['coordinates'][0][1][0] += .01
        with self.assertRaises(ValueError): validate_test_geography(item)
        item=deepcopy(self.data); item['camps'][0]['location_source']='javascript:alert(1)'
        with self.assertRaises(ValueError): validate_test_geography(item)
        item=deepcopy(self.data); item['zones']['features'][0]['properties']['color']='red;display:none'
        with self.assertRaises(ValueError): validate_test_geography(item)

    def test_site_names_are_escaped_and_ledger_identity_is_preserved(self):
        snapshot={'node':'B','read_only':False,'state':initial_state(),'events':[]}
        data=deepcopy(self.data); data['camps'][0]['host_name']='<script>alert(1)</script>'
        with patch('sanjay.dashboard.load_test_geography',return_value=data):
            page=render(snapshot,'token')
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',page)
        self.assertNotIn('<script>alert(1)</script>',page)
        self.assertIn('Authoring node: Camp B',page)
        self.assertEqual(page.count('data-select-camp='),4)
        self.assertIn('id="show-zones"',page)
        self.assertIn('id="show-camps"',page)
        self.assertIn('id="fit-zones"',page)
        self.assertIn('not assigned to a geographic test camp',page)
        self.assertIn('No pending or rejected local events',page)


if __name__ == '__main__':
    unittest.main()
