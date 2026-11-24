"""Recording-stage acceptance on two local stores, not physical computers/USB."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

from sanjay.store import Store
from sanjay.workflow import WorkflowError


class FirstThreeTests(unittest.TestCase):
    def setUp(self):
        scratch = Path.home() / 'AppData/Local/hermes/cache/scratch'
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='first-three-', dir=scratch)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.a = Store.create(root / 'A', 'A')
        self.b = Store.create(root / 'B', 'B')
        self.a.trust((self.b.folder / 'public.json').read_bytes())
        self.b.trust((self.a.folder / 'public.json').read_bytes())
        self.a.command('stock', {'quantity': 200})
        self.request_id = str(uuid4())
        self.grant_id = str(uuid4())
        self.dispatch_id = str(uuid4())
        self.b.command('request', {'request_id': self.request_id, 'quantity': 100})

    def allocate(self):
        self.b.command('grant', {'grant_id': self.grant_id,
                                'request_id': self.request_id,
                                'supplier': 'A', 'quantity': 100})
        self.a.import_bundle(self.b.export_bundle())

    def assert_stock(self, store, on_hand, reserved, available):
        inventory = store.snapshot()['state']['stocks']['A']
        self.assertEqual(inventory, {'on_hand': on_hand, 'reserved': reserved})
        self.assertEqual(inventory['on_hand'] - inventory['reserved'], available)

    def reject_unchanged(self, store, kind, payload):
        before = store.snapshot()
        exported = store.export_bundle()
        with self.assertRaises(WorkflowError):
            store.command(kind, payload)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(store.export_bundle(), exported)
        self.assertEqual(Store(store.folder).snapshot(), before)

    def test_stage1_request_survives_store_and_process_restart(self):
        before = self.b.snapshot()
        self.assertEqual(before['state']['requests'][self.request_id],
                         {'owner': 'B', 'quantity': 100, 'granted': 0, 'received': 0})
        self.assertEqual(Store(self.b.folder).snapshot(), before)
        reopened = subprocess.run(
            [sys.executable, '-B', '-c',
             'import json, sys; from sanjay.store import Store; '
             'print(json.dumps(Store(sys.argv[1]).snapshot()))', str(self.b.folder)],
            cwd=Path(__file__).resolve().parents[1],
            check=True, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(json.loads(reopened.stdout), before)
        self.assertEqual(before['events'][0]['kind'], 'request')
        self.assertEqual(before['events'][0]['status'], 'applied')
        self.assertEqual(before['state']['grants'], {})
        self.assertEqual(before['state']['dispatches'], {})

    def test_stage2_allocation_then_acceptance_reserves_without_departure(self):
        self.allocate()
        self.assert_stock(self.a, 200, 0, 200)
        state = self.a.snapshot()['state']
        self.assertEqual(state['grants'][self.grant_id]['status'], 'granted')
        self.assertEqual(state['requests'][self.request_id]['granted'], 100)
        self.a.command('accept', {'grant_id': self.grant_id})
        self.assert_stock(self.a, 200, 100, 100)
        state = Store(self.a.folder).snapshot()['state']
        self.assertEqual(state['grants'][self.grant_id]['status'], 'accepted')
        self.assertEqual(state['dispatches'], {})
        self.assertEqual(state['receipts'], {})
        self.b.import_bundle(self.a.export_bundle())
        self.assertEqual(self.b.snapshot()['state'], state)

    def test_stage3_approval_preserves_stock_and_departure_debits_once(self):
        self.allocate()
        self.a.command('accept', {'grant_id': self.grant_id})
        accepted = self.a.snapshot()['state']
        approval = self.a.command('approve', {'grant_id': self.grant_id})
        approved = self.a.snapshot()['state']
        self.assertEqual(approved['stocks'], accepted['stocks'])
        self.assert_stock(self.a, 200, 100, 100)
        self.assertEqual(approved['grants'][self.grant_id]['status'], 'approved')
        self.assertEqual(approved['dispatches'], {})
        departure = self.a.command('dispatch', {'dispatch_id': self.dispatch_id,
                                               'grant_id': self.grant_id, 'quantity': 100})
        self.assertEqual(departure['seq'], approval['seq'] + 1)
        self.assert_stock(self.a, 100, 0, 100)
        state = self.a.snapshot()['state']
        self.assertEqual(state['grants'][self.grant_id]['status'], 'dispatched')
        self.assertEqual(state['dispatches'][self.dispatch_id],
                         {'grant_id': self.grant_id, 'supplier': 'A', 'destination': 'B',
                          'quantity': 100, 'received': 0})
        self.assertEqual(state['requests'][self.request_id]['received'], 0)
        self.assertEqual(state['receipts'], {})
        self.assertEqual(state['stocks'].get('B', {'on_hand': 0})['on_hand'], 0)
        self.assertEqual(Store(self.a.folder).snapshot()['state'], state)
        self.b.import_bundle(self.a.export_bundle())
        self.b.import_bundle(self.a.export_bundle())
        self.assertEqual(self.b.snapshot()['state'], state)

    def test_wrong_origin_actions_leave_durable_state_and_log_unchanged(self):
        self.a.import_bundle(self.b.export_bundle())
        self.reject_unchanged(self.a, 'grant',
                              {'grant_id': self.grant_id, 'request_id': self.request_id,
                               'supplier': 'B', 'quantity': 100})
        self.allocate()
        self.reject_unchanged(self.b, 'accept', {'grant_id': self.grant_id})
        self.a.command('accept', {'grant_id': self.grant_id})
        self.b.import_bundle(self.a.export_bundle())
        self.reject_unchanged(self.b, 'approve', {'grant_id': self.grant_id})
        self.a.command('approve', {'grant_id': self.grant_id})
        self.b.import_bundle(self.a.export_bundle())
        self.reject_unchanged(self.b, 'dispatch',
                              {'dispatch_id': self.dispatch_id, 'grant_id': self.grant_id,
                               'quantity': 100})

    def test_invalid_transitions_leave_no_events_or_sequence_gaps(self):
        self.allocate()
        dispatch = {'dispatch_id': self.dispatch_id, 'grant_id': self.grant_id, 'quantity': 100}
        self.reject_unchanged(self.a, 'approve', {'grant_id': self.grant_id})
        self.reject_unchanged(self.a, 'dispatch', dispatch)
        accepted = self.a.command('accept', {'grant_id': self.grant_id})
        self.assertEqual(accepted['seq'], 2)
        self.reject_unchanged(self.a, 'accept', {'grant_id': self.grant_id})
        self.reject_unchanged(self.a, 'dispatch', dispatch)
        approved = self.a.command('approve', {'grant_id': self.grant_id})
        self.assertEqual(approved['seq'], 3)
        self.reject_unchanged(self.a, 'approve', {'grant_id': self.grant_id})
        for quantity in (99, 101):
            with self.subTest(quantity=quantity):
                self.reject_unchanged(self.a, 'dispatch', {**dispatch, 'quantity': quantity})
        departed = self.a.command('dispatch', dispatch)
        self.assertEqual(departed['seq'], 4)
        self.reject_unchanged(self.a, 'dispatch', dispatch)
        self.reject_unchanged(self.a, 'dispatch', {**dispatch, 'dispatch_id': str(uuid4())})
        self.assert_stock(self.a, 100, 0, 100)


if __name__ == '__main__':
    unittest.main()
