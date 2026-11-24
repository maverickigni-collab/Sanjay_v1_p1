"""UI contracts, including honest map authority and unavailable capabilities."""
from copy import deepcopy
import unittest

from sanjay.ui import render
from sanjay.workflow import initial_state


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'node': 'A', 'read_only': False, 'state': initial_state(), 'events': []}
        self.snapshot['state']['stocks']['A'] = {'on_hand': 200, 'reserved': 100}

    def test_map_is_default_and_invalid_views_fall_back(self):
        for view in ('map', 'not-a-view', '<script>'):
            page = render(self.snapshot, 'test-token', view=view)
            self.assertIn('data-view="map"', page)
            self.assertIn('id="operations-map"', page)
            self.assertIn('Authoring node: Camp A', page)
            self.assertIn('not for navigation', page.lower())
        self.assertIn('data-view="map"', render(self.snapshot, 'token'))

    def test_separate_layers_and_no_invented_stock_or_report_counts(self):
        page = render(self.snapshot, 'token')
        self.assertIn('id="map-source"', page)
        self.assertIn('Satellite · offline 10m', page)
        self.assertIn('Street map · online', page)
        self.assertNotIn('data-map-camp=', page)
        self.assertNotIn('data-layer="perimeters"', page)
        self.assertIn('provisional point', page)
        self.assertIn('not surveyed or drawn', page)
        self.assertIn('<dd>200</dd>', page)
        self.assertIn('<dd>100</dd>', page)
        self.assertIn('<dd>Unknown</dd>', page)
        self.assertIn('No pending or rejected local events', page)
        self.assertNotIn('2 new reports', page)
        self.assertNotIn('data-kind="dispatch"', page)
        self.assertIn('Review supply requests', page)

    def test_remote_inspector_cannot_author_dispatch(self):
        page = render(self.snapshot, 'token')
        self.assertEqual(page.count('data-select-camp='), 4)
        for direction in ('south', 'north', 'east', 'west'):
            self.assertIn(f'data-camp-detail="{direction}"', page)
        self.assertIn('Authoring node: Camp A', page)
        self.assertIn('not assigned to a geographic test camp', page)
        self.assertNotIn('data-kind="dispatch"', page)

    def test_messages_are_escaped_and_render_does_not_mutate_snapshot(self):
        before = deepcopy(self.snapshot)
        page = render(self.snapshot, 'token', '<img src=x onerror=alert(1)>')
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', page)
        self.assertNotIn('<img src=x', page)
        self.assertEqual(before, self.snapshot)

    def test_reports_and_routing_are_not_fake_features(self):
        reports = render(self.snapshot, 'token', view='reports')
        self.assertIn('Phones cannot submit reports yet', reports)
        self.assertNotIn('id="report-submit"', reports)
        dispatch = render(self.snapshot, 'token', view='dispatch')
        self.assertIn('Road options are not available', dispatch)

    def test_actual_review_counts_and_recovery_state(self):
        self.snapshot['events'] = [
            {'origin': 'A', 'kind': 'stock', 'status': 'pending', 'seq': 1, 'reason': ''},
            {'origin': 'B', 'kind': 'stock', 'status': 'rejected', 'seq': 1, 'reason': '<unsafe>'},
        ]
        self.snapshot['read_only'] = True
        page = render(self.snapshot, 'token')
        self.assertIn('1 pending · 1 requiring review', page)
        self.assertIn('inspection only', page)
        self.assertNotIn('<form', page)

    def test_theme_accessibility_and_local_assets(self):
        page = render(self.snapshot, 'token')
        self.assertIn('data-theme="dark"', page)
        self.assertIn('id="theme-toggle"', page)
        self.assertIn('aria-pressed="false"', page)
        self.assertIn('nonce="token"', page)
        self.assertIn('href="/assets/app.css"', page)
        self.assertIn('tabindex="0" role="group"', page)


if __name__ == '__main__':
    unittest.main()
