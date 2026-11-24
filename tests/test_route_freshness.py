"""Advisory acknowledgement and expiry cannot debit stock."""
import datetime as dt
import unittest
import test_operations as operations
from sanjay.registry import RegistryError

class RouteFreshnessTests(unittest.TestCase):
    setUp=operations.OperationsTests.setUp
    tearDown=operations.OperationsTests.tearDown
    approved=operations.OperationsTests.approved
    def test_explicit_acknowledgement_and_expired_choice_fail_closed(self):
        _,grant=self.approved();route=self.ops.routes('south',grant)['routes'][0]
        with self.assertRaises(RegistryError):self.ops.choose_route('south',grant,route['route_id'],False)
        self.ops.choose_route('south',grant,route['route_id'],True)
        choice=self.ops.registry.get_route_choice(grant)
        choice['selected_at']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=31)).isoformat()
        self.ops.registry.save_route_choice(grant,choice);before=self.ops.nodes['south'].snapshot()
        with self.assertRaisesRegex(RegistryError,'expired'):self.ops.command('south','dispatch',{'grant_id':grant})
        self.assertEqual(before,self.ops.nodes['south'].snapshot())

    def test_restart_requires_fresh_route_review_before_departure(self):
        _,grant=self.approved();route=self.ops.routes('south',grant)['routes'][0]
        self.ops.choose_route('south',grant,route['route_id'],True);before=self.ops.nodes['south'].snapshot()
        self.ops.close();self.ops=operations.Operations(self.root)
        with self.assertRaisesRegex(RegistryError,'earlier session'):self.ops.command('south','dispatch',{'grant_id':grant})
        self.assertEqual(before,self.ops.nodes['south'].snapshot())

if __name__=='__main__':unittest.main()
