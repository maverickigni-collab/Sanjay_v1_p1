"""Executed acceptance tests use only isolated fictional node directories."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from sanjay.store import ExchangeError, Store, encode
from sanjay.workflow import WorkflowError


def uid():
    return str(uuid.uuid4())


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        scratch = Path.home() / 'AppData/Local/hermes/cache/scratch'
        scratch.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.tmp.name)
        self.a = Store.create(self.root / 'A', 'A')
        self.b = Store.create(self.root / 'B', 'B')
        self.a.trust((self.b.folder / 'public.json').read_bytes())
        self.b.trust((self.a.folder / 'public.json').read_bytes())

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, source, destination):
        return destination.import_bundle(source.export_bundle())

    def prepare_dispatch(self):
        self.a.command('stock', {'quantity': 200})
        request, grant, dispatch = uid(), uid(), uid()
        self.b.command('request', {'request_id': request, 'quantity': 100})
        self.b.command('grant', {'grant_id': grant, 'request_id': request, 'supplier': 'A', 'quantity': 100})
        self.send(self.b, self.a)
        self.a.command('accept', {'grant_id': grant})
        self.a.command('approve', {'grant_id': grant})
        self.a.command('dispatch', {'dispatch_id': dispatch, 'grant_id': grant, 'quantity': 100})
        return request, grant, dispatch

    def test_full_story_persistence_replay_and_backup(self):
        request, _, dispatch = self.prepare_dispatch()
        self.send(self.a, self.b)
        self.b.command('receipt', {'receipt_id': uid(), 'dispatch_id': dispatch, 'quantity': 90})
        self.send(self.b, self.a)
        self.send(self.b, self.a)
        self.send(self.a, self.b)
        for store in (Store(self.a.folder), Store(self.b.folder)):
            state = store.snapshot()['state']
            self.assertEqual(state['stocks']['A'], {'on_hand': 100, 'reserved': 0})
            self.assertEqual(state['stocks']['B'], {'on_hand': 90, 'reserved': 0})
            self.assertEqual(state['requests'][request]['quantity'] - state['requests'][request]['received'], 10)
            self.assertEqual(state['dispatches'][dispatch]['quantity'] - state['dispatches'][dispatch]['received'], 10)
        backup = self.a.backup(self.root / 'backup.sqlite')
        copy = Store.inspect_backup(backup, self.root / 'recovery')
        self.assertEqual(copy.snapshot()['state'], self.a.snapshot()['state'])
        with self.assertRaises(ExchangeError):
            copy.command('request', {'request_id': uid(), 'quantity': 1})
        with self.assertRaises(ExchangeError):
            copy.import_bundle(self.b.export_bundle())
        with self.assertRaises(ExchangeError):
            self.a.backup(backup)

    def test_tampered_bundle_rolls_back_all_events(self):
        self.a.command('stock', {'quantity': 200})
        self.a.command('request', {'request_id': uid(), 'quantity': 1})
        data = json.loads(self.a.export_bundle())
        event = json.loads(base64.b64decode(data['events'][1]['data']))
        event['payload']['quantity'] = 9
        data['events'][1]['data'] = base64.b64encode(encode(event)).decode()
        before = self.b.snapshot()
        with self.assertRaises(ExchangeError):
            self.b.import_bundle(encode(data))
        self.assertEqual(before, self.b.snapshot())

    def receiver_fixture(self):
        # Test-only receive replica of B; never authors events or receives a private key.
        folder = self.root / 'receive-fixture'
        folder.mkdir()
        self.b.backup(folder / 'node.sqlite')
        return Store(folder)

    def test_out_of_order_origin_gap_then_dependency(self):
        receiver = self.receiver_fixture()
        _, _, dispatch = self.prepare_dispatch()
        self.send(self.a, self.b)
        self.b.command('receipt', {'receipt_id': uid(), 'dispatch_id': dispatch, 'quantity': 90})
        wrapped = json.loads(self.b.export_bundle())['events']
        receipt = [w for w in wrapped if json.loads(base64.b64decode(w['data']))['kind'] == 'receipt'][0]
        result = receiver.import_bundle(encode({'version': 1, 'events': [receipt]}))
        self.assertEqual(result[0]['status'], 'pending')
        self.assertEqual(receiver.snapshot()['state']['receipts'], {})
        self.send(self.b, receiver)
        self.assertTrue(all(r['status'] == 'applied' for r in receiver.snapshot()['events']))
        self.assertEqual(receiver.snapshot()['state']['stocks']['B']['on_hand'], 90)

    def test_missing_dispatch_dependency_retries(self):
        receiver = self.receiver_fixture()
        request, _, dispatch = self.prepare_dispatch()
        self.send(self.a, self.b)
        self.b.command('receipt', {'receipt_id': uid(), 'dispatch_id': dispatch, 'quantity': 90})
        events = json.loads(self.b.export_bundle())['events']
        b_only = [w for w in events if json.loads(base64.b64decode(w['data']))['origin'] == 'B']
        receiver.import_bundle(encode({'version': 1, 'events': b_only}))
        snap = receiver.snapshot()
        self.assertEqual(snap['state']['requests'][request]['received'], 0)
        self.assertEqual(snap['events'][-1]['status'], 'pending')
        self.send(self.a, receiver)
        state = receiver.snapshot()['state']
        self.assertEqual(state['requests'][request]['received'], 90)
        self.assertEqual(state['stocks']['B']['on_hand'], 90)

    def test_untrusted_and_key_replacement(self):
        self.a.command('stock', {'quantity': 200})
        stranger = Store.create(self.root / 'stranger', 'B')
        with self.assertRaises(ExchangeError):
            stranger.import_bundle(self.a.export_bundle())
        alternate = Store.create(self.root / 'alternate', 'A')
        with self.assertRaises(ExchangeError):
            self.b.trust((alternate.folder / 'public.json').read_bytes())

    def test_duplicate_json_and_size_rejected(self):
        for raw in [b'{"version":1,"version":1,"events":[]}', b'x' * 1_000_001,
                    b'{"version":true,"events":[]}', b'{"version":1,"events":{}}']:
            with self.assertRaises(ExchangeError):
                self.b.import_bundle(raw)
        self.assertEqual(self.b.snapshot()['events'], [])

    def test_collision_preserves_original(self):
        self.a.command('stock', {'quantity': 200})
        self.send(self.a, self.b)
        bundle = json.loads(self.a.export_bundle())
        event = json.loads(base64.b64decode(bundle['events'][0]['data']))
        event['payload']['quantity'] = 201
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.from_private_bytes((self.a.folder / 'private.key').read_bytes())
        raw = encode(event)
        bundle['events'][0] = {'data': base64.b64encode(raw).decode(), 'signature': base64.b64encode(key.sign(raw)).decode()}
        before = self.b.snapshot()
        with self.assertRaises(ExchangeError):
            self.b.import_bundle(encode(bundle))
        self.assertEqual(before, self.b.snapshot())

    def test_signed_bad_schema_rejects_entire_bundle(self):
        self.a.command('stock', {'quantity': 200})
        self.a.command('request', {'request_id': uid(), 'quantity': 1})
        original = json.loads(self.a.export_bundle())
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.from_private_bytes((self.a.folder / 'private.key').read_bytes())
        for bad_payload in [{'request_id': uid(), 'quantity': True},
                            {'request_id': uid(), 'quantity': 1, 'extra': 'ignored?'},
                            {'request_id': uid(), 'quantity': '1'}]:
            with self.subTest(payload=bad_payload):
                bundle = json.loads(json.dumps(original))
                event = json.loads(base64.b64decode(bundle['events'][1]['data']))
                event['payload'] = bad_payload
                raw = encode(event)
                bundle['events'][1] = {'data': base64.b64encode(raw).decode(), 'signature': base64.b64encode(key.sign(raw)).decode()}
                before = self.b.snapshot()
                with self.assertRaises(ExchangeError):
                    self.b.import_bundle(encode(bundle))
                self.assertEqual(before, self.b.snapshot())

    def test_concurrent_reservations_cannot_oversubscribe(self):
        self.a.command('stock', {'quantity': 100})
        grants = []
        for _ in range(2):
            request, grant = uid(), uid()
            self.b.command('request', {'request_id': request, 'quantity': 80})
            self.b.command('grant', {'grant_id': grant, 'request_id': request, 'supplier': 'A', 'quantity': 80})
            grants.append(grant)
        self.send(self.b, self.a)
        def accept(grant):
            try:
                Store(self.a.folder).command('accept', {'grant_id': grant})
                return True
            except WorkflowError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(accept, grants))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(self.a.snapshot()['state']['stocks']['A']['reserved'], 80)


if __name__ == '__main__':
    unittest.main()
