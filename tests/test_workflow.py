import json
import unittest
from copy import deepcopy
from uuid import uuid4

from sanjay.workflow import MissingDependency, WorkflowError, apply_event, initial_state, validate_payload


def uid():
    return str(uuid4())


def event(kind, origin, **payload):
    return {"v": 1, "id": uid(), "origin": origin, "seq": 1,
            "at": "2026-11-19T12:00:00Z", "kind": kind, "payload": payload}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.state = initial_state()
        self.request_id, self.grant_id, self.dispatch_id = uid(), uid(), uid()

    def apply(self, kind, origin, **payload):
        command = event(kind, origin, **payload)
        before, event_before = deepcopy(self.state), deepcopy(command)
        result = apply_event(self.state, command)
        self.assertEqual(self.state, before)
        self.assertEqual(command, event_before)
        self.assertIsNot(result, self.state)
        self.assertEqual(json.loads(json.dumps(result)), result)
        for inventory in result["stocks"].values():
            self.assertGreaterEqual(inventory["reserved"], 0)
            self.assertGreaterEqual(inventory["on_hand"], inventory["reserved"])
        self.state = result

    def reject(self, command, error=WorkflowError):
        before, event_before = deepcopy(self.state), deepcopy(command)
        with self.assertRaises(error) as caught:
            apply_event(self.state, command)
        self.assertIs(type(caught.exception), error)
        self.assertEqual(self.state, before)
        self.assertEqual(command, event_before)

    def grant(self, quantity=100, stock=200):
        self.apply("stock", "A", quantity=stock)
        self.apply("request", "B", request_id=self.request_id, quantity=quantity)
        self.apply("grant", "B", grant_id=self.grant_id, request_id=self.request_id,
                   supplier="A", quantity=quantity)

    def dispatch(self):
        self.grant()
        self.apply("accept", "A", grant_id=self.grant_id)
        self.apply("approve", "A", grant_id=self.grant_id)
        self.apply("dispatch", "A", dispatch_id=self.dispatch_id,
                   grant_id=self.grant_id, quantity=100)

    def test_initial_state_is_fresh_and_exact(self):
        self.assertEqual(self.state, {"stocks": {}, "requests": {}, "grants": {},
                                     "dispatches": {}, "receipts": {}})
        other = initial_state()
        other["stocks"]["A"] = {"on_hand": 1, "reserved": 0}
        self.assertEqual(self.state["stocks"], {})
        self.assertTrue(issubclass(WorkflowError, ValueError))
        self.assertTrue(issubclass(MissingDependency, WorkflowError))

    def test_200_100_90_scenario_and_exact_records(self):
        self.grant()
        self.assertEqual(self.state["stocks"]["A"], {"on_hand": 200, "reserved": 0})
        self.apply("accept", "A", grant_id=self.grant_id)
        self.assertEqual(self.state["stocks"]["A"], {"on_hand": 200, "reserved": 100})
        self.apply("approve", "A", grant_id=self.grant_id)
        self.assertEqual(self.state["stocks"]["A"], {"on_hand": 200, "reserved": 100})
        self.apply("dispatch", "A", dispatch_id=self.dispatch_id, grant_id=self.grant_id, quantity=100)
        receipt_id = uid()
        self.apply("receipt", "B", receipt_id=receipt_id, dispatch_id=self.dispatch_id, quantity=90)
        self.assertEqual(self.state["stocks"], {"A": {"on_hand": 100, "reserved": 0},
                                                "B": {"on_hand": 90, "reserved": 0}})
        self.assertEqual(self.state["requests"][self.request_id],
                         {"owner": "B", "quantity": 100, "granted": 100, "received": 90})
        self.assertEqual(self.state["grants"][self.grant_id],
                         {"request_id": self.request_id, "supplier": "A", "quantity": 100,
                          "status": "dispatched"})
        self.assertEqual(self.state["dispatches"][self.dispatch_id],
                         {"grant_id": self.grant_id, "supplier": "A", "destination": "B",
                          "quantity": 100, "received": 90})
        self.assertEqual(self.state["receipts"][receipt_id],
                         {"dispatch_id": self.dispatch_id, "quantity": 90, "origin": "B"})
        request = self.state["requests"][self.request_id]
        dispatch = self.state["dispatches"][self.dispatch_id]
        self.assertEqual(request["quantity"] - request["received"], 10)
        self.assertEqual(dispatch["quantity"] - dispatch["received"], 10)

    def test_initial_stock_once_even_after_exhaustion(self):
        self.grant(stock=100)
        self.apply("accept", "A", grant_id=self.grant_id)
        self.apply("approve", "A", grant_id=self.grant_id)
        self.apply("dispatch", "A", dispatch_id=self.dispatch_id, grant_id=self.grant_id, quantity=100)
        self.assertEqual(self.state["stocks"]["A"]["on_hand"], 0)
        self.reject(event("stock", "A", quantity=1))

    def test_receipt_does_not_count_as_initial_stock(self):
        self.dispatch()
        self.apply("receipt", "B", receipt_id=uid(), dispatch_id=self.dispatch_id, quantity=90)
        self.apply("stock", "B", quantity=20)
        self.assertEqual(self.state["stocks"]["B"], {"on_hand": 110, "reserved": 0})
        self.reject(event("stock", "B", quantity=20))

    def test_reservations_prevent_oversubscription(self):
        self.grant(quantity=150)
        self.apply("accept", "A", grant_id=self.grant_id)
        second_request, second_grant = uid(), uid()
        self.apply("request", "B", request_id=second_request, quantity=51)
        self.apply("grant", "B", grant_id=second_grant, request_id=second_request, supplier="A", quantity=51)
        self.reject(event("accept", "A", grant_id=second_grant))
        self.assertEqual(self.state["stocks"]["A"], {"on_hand": 200, "reserved": 150})

    def test_unknown_supplier_has_zero_available_stock(self):
        self.apply("request", "B", request_id=self.request_id, quantity=1)
        self.apply("grant", "B", grant_id=self.grant_id, request_id=self.request_id, supplier="A", quantity=1)
        self.reject(event("accept", "A", grant_id=self.grant_id))

    def test_grants_cannot_exceed_request_in_total(self):
        self.apply("request", "B", request_id=self.request_id, quantity=100)
        self.apply("grant", "B", grant_id=self.grant_id, request_id=self.request_id, supplier="A", quantity=60)
        self.reject(event("grant", "B", grant_id=uid(), request_id=self.request_id, supplier="A", quantity=41))
        self.apply("grant", "B", grant_id=uid(), request_id=self.request_id, supplier="A", quantity=40)
        self.assertEqual(self.state["requests"][self.request_id]["granted"], 100)

    def test_accept_and_separate_approval_required(self):
        self.grant()
        self.reject(event("approve", "A", grant_id=self.grant_id))
        self.reject(event("dispatch", "A", dispatch_id=self.dispatch_id, grant_id=self.grant_id, quantity=100))
        self.apply("accept", "A", grant_id=self.grant_id)
        self.reject(event("dispatch", "A", dispatch_id=self.dispatch_id, grant_id=self.grant_id, quantity=100))
        self.apply("approve", "A", grant_id=self.grant_id)
        self.apply("dispatch", "A", dispatch_id=self.dispatch_id, grant_id=self.grant_id, quantity=100)

    def test_dispatch_is_full_and_once(self):
        self.grant()
        self.apply("accept", "A", grant_id=self.grant_id)
        self.apply("approve", "A", grant_id=self.grant_id)
        for quantity in (99, 101):
            self.reject(event("dispatch", "A", dispatch_id=uid(), grant_id=self.grant_id, quantity=quantity))
        self.apply("dispatch", "A", dispatch_id=self.dispatch_id, grant_id=self.grant_id, quantity=100)
        self.reject(event("dispatch", "A", dispatch_id=uid(), grant_id=self.grant_id, quantity=100))

    def test_partial_receipts_accumulate_without_overrun(self):
        self.dispatch()
        self.apply("receipt", "B", receipt_id=uid(), dispatch_id=self.dispatch_id, quantity=60)
        self.reject(event("receipt", "B", receipt_id=uid(), dispatch_id=self.dispatch_id, quantity=41))
        self.apply("receipt", "B", receipt_id=uid(), dispatch_id=self.dispatch_id, quantity=40)
        self.reject(event("receipt", "B", receipt_id=uid(), dispatch_id=self.dispatch_id, quantity=1))
        self.assertEqual(self.state["stocks"]["B"]["on_hand"], 100)
        self.assertEqual(self.state["requests"][self.request_id]["received"], 100)

    def test_wrong_origins_and_self_supply_rejected(self):
        self.apply("request", "B", request_id=self.request_id, quantity=100)
        self.reject(event("grant", "A", grant_id=self.grant_id, request_id=self.request_id, supplier="B", quantity=100))
        self.reject(event("grant", "B", grant_id=self.grant_id, request_id=self.request_id, supplier="B", quantity=100))
        self.state = initial_state()
        self.grant()
        self.reject(event("accept", "B", grant_id=self.grant_id))
        self.apply("accept", "A", grant_id=self.grant_id)
        self.reject(event("approve", "B", grant_id=self.grant_id))
        self.apply("approve", "A", grant_id=self.grant_id)
        self.reject(event("dispatch", "B", dispatch_id=self.dispatch_id, grant_id=self.grant_id, quantity=100))
        self.apply("dispatch", "A", dispatch_id=self.dispatch_id, grant_id=self.grant_id, quantity=100)
        for origin in ("A", "HQ", None, [], True):
            self.reject(event("receipt", origin, receipt_id=uid(), dispatch_id=self.dispatch_id, quantity=90))

    def test_missing_records_are_missing_dependencies(self):
        commands = [
            event("grant", "B", grant_id=uid(), request_id=uid(), supplier="A", quantity=1),
            event("accept", "A", grant_id=uid()),
            event("approve", "A", grant_id=uid()),
            event("dispatch", "A", dispatch_id=uid(), grant_id=uid(), quantity=1),
            event("receipt", "B", receipt_id=uid(), dispatch_id=uid(), quantity=1),
        ]
        for command in commands:
            with self.subTest(kind=command["kind"]):
                self.reject(command, MissingDependency)

    def test_pending_receipt_can_apply_after_dependency_arrives(self):
        receipt = event("receipt", "B", receipt_id=uid(), dispatch_id=self.dispatch_id, quantity=90)
        self.reject(receipt, MissingDependency)
        self.dispatch()
        self.state = apply_event(self.state, receipt)
        self.assertEqual(self.state["stocks"]["B"]["on_hand"], 90)

    def test_duplicate_ids_and_transitions_do_not_repeat_effects(self):
        self.grant()
        self.reject(event("request", "B", request_id=self.request_id, quantity=100))
        self.reject(event("grant", "B", grant_id=self.grant_id, request_id=self.request_id, supplier="A", quantity=100))
        self.apply("accept", "A", grant_id=self.grant_id)
        self.reject(event("accept", "A", grant_id=self.grant_id))
        self.apply("approve", "A", grant_id=self.grant_id)
        self.reject(event("approve", "A", grant_id=self.grant_id))
        self.apply("dispatch", "A", dispatch_id=self.dispatch_id, grant_id=self.grant_id, quantity=100)
        self.reject(event("dispatch", "A", dispatch_id=self.dispatch_id, grant_id=self.grant_id, quantity=100))
        receipt_id = uid()
        self.apply("receipt", "B", receipt_id=receipt_id, dispatch_id=self.dispatch_id, quantity=90)
        self.reject(event("receipt", "B", receipt_id=receipt_id, dispatch_id=self.dispatch_id, quantity=10))

    def test_payload_keys_types_and_referenced_uuid_validation(self):
        def reject_payload(command):
            before = deepcopy(command)
            with self.assertRaises(WorkflowError) as caught:
                validate_payload(command["kind"], command["payload"])
            self.assertIs(type(caught.exception), WorkflowError)
            self.assertEqual(command, before)
            self.reject(command)

        commands = [
            event("stock", "A", quantity=1),
            event("request", "B", request_id=uid(), quantity=1),
            event("grant", "B", grant_id=uid(), request_id=uid(), supplier="A", quantity=1),
            event("accept", "A", grant_id=uid()),
            event("approve", "A", grant_id=uid()),
            event("dispatch", "A", dispatch_id=uid(), grant_id=uid(), quantity=1),
            event("receipt", "B", receipt_id=uid(), dispatch_id=uid(), quantity=1),
        ]
        for command in commands:
            before = deepcopy(command)
            self.assertIsNone(validate_payload(command["kind"], command["payload"]))
            self.assertEqual(command, before)
            for key in command["payload"]:
                invalid = deepcopy(command)
                del invalid["payload"][key]
                with self.subTest(kind=command["kind"], missing=key):
                    reject_payload(invalid)
            for extra in ("trusted", "status", "owner"):
                invalid = deepcopy(command)
                invalid["payload"][extra] = True
                reject_payload(invalid)
            for key in command["payload"]:
                invalid_values = ([0, -1, True, False, 1.0, "1", None, [], {}]
                                  if key == "quantity" else
                                  ["", "not-a-uuid", 1, True, None, [], {}]
                                  if key.endswith("_id") else ["HQ", "", 1, None, []])
                for value in invalid_values:
                    invalid = deepcopy(command)
                    invalid["payload"][key] = value
                    with self.subTest(kind=command["kind"], key=key, value=value):
                        reject_payload(invalid)
            for payload in (None, [], "quantity", 1):
                invalid = deepcopy(command)
                invalid["payload"] = payload
                reject_payload(invalid)

    def test_validate_payload_rejects_unknown_and_malformed_kinds(self):
        for kind in ("", "unknown", None, True, 1, [], {}):
            with self.subTest(kind=kind):
                payload = {"quantity": 1}
                with self.assertRaises(WorkflowError) as caught:
                    validate_payload(kind, payload)
                self.assertIs(type(caught.exception), WorkflowError)
                self.assertEqual(payload, {"quantity": 1})

    def test_malformed_event_and_kind(self):
        for command in (None, [], 1, {}, {"kind": []}, event("unknown", "A")):
            self.reject(command)

    def test_returned_nested_state_and_event_are_detached(self):
        self.dispatch()
        command = event("receipt", "B", receipt_id=uid(), dispatch_id=self.dispatch_id, quantity=90)
        before = deepcopy(self.state)
        result = apply_event(self.state, command)
        for records in result.values():
            for record in records.values():
                record["test_mutation"] = True
        self.assertEqual(self.state, before)
        command["payload"]["quantity"] = 999
        self.assertEqual(result["receipts"][command["payload"]["receipt_id"]]["quantity"], 90)


if __name__ == "__main__":
    unittest.main()
