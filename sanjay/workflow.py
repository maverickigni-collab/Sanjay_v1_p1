"""Pure relief-workflow transitions; envelope authentication belongs to storage."""

from copy import deepcopy
from uuid import UUID

from .identities import NODES


class WorkflowError(ValueError):
    """An invalid command or permanent workflow conflict."""


class MissingDependency(WorkflowError):
    """A referenced workflow record has not arrived yet."""


def initial_state():
    return {key: {} for key in ("stocks", "requests", "grants", "dispatches", "receipts")}


_PAYLOAD_KEYS = {
    "stock": {"quantity"},
    "request": {"request_id", "quantity"},
    "grant": {"grant_id", "request_id", "supplier", "quantity"},
    "accept": {"grant_id"},
    "approve": {"grant_id"},
    "dispatch": {"dispatch_id", "grant_id", "quantity"},
    "receipt": {"receipt_id", "dispatch_id", "quantity"},
}


def _record(records, record_id):
    if record_id not in records:
        raise MissingDependency(f"Missing workflow record: {record_id}")
    return records[record_id]


def _require(condition, message):
    if not condition:
        raise WorkflowError(message)


def validate_payload(kind, payload):
    """Validate payload schema without state; return None or raise WorkflowError.

    Does not mutate the payload or check ownership, dependencies or transitions.
    """
    _require(isinstance(kind, str) and kind in _PAYLOAD_KEYS, "Unknown event kind")
    _require(isinstance(payload, dict), "Payload must be a dictionary")
    _require(set(payload) == _PAYLOAD_KEYS[kind], "Incorrect payload keys")
    for key, value in payload.items():
        if key.endswith("_id"):
            _require(isinstance(value, str), f"{key} must be a UUID string")
            try:
                UUID(value)
            except ValueError as exc:
                raise WorkflowError(f"{key} must be a UUID string") from exc
        elif key == "quantity":
            _require(type(value) is int and value > 0, "Quantity must be a positive integer")
        elif key == "supplier":
            _require(isinstance(value, str) and value in NODES, "Invalid supplier")


def apply_event(state, event):
    """Return detached state from reducer-owned state and an authenticated envelope.

    Storage validates envelope UUID, version, sequence, timestamp and signature,
    and handles byte-identical event replay before invoking this reducer.
    """
    _require(isinstance(event, dict), "Event must be a dictionary")
    kind = event.get("kind")
    origin = event.get("origin")
    payload = event.get("payload")
    validate_payload(kind, payload)
    _require(isinstance(origin, str) and origin in NODES, "Invalid origin")

    result = deepcopy(state)
    stocks = result["stocks"]
    requests = result["requests"]
    grants = result["grants"]
    dispatches = result["dispatches"]
    receipts = result["receipts"]

    if kind == "stock":
        inventory = stocks.setdefault(origin, {"on_hand": 0, "reserved": 0})
        # Conservation identifies prior initial stock even after it is exhausted.
        initial_quantity = (
            inventory["on_hand"]
            + sum(d["quantity"] for d in dispatches.values() if d["supplier"] == origin)
            - sum(r["quantity"] for r in receipts.values() if r["origin"] == origin)
        )
        _require(initial_quantity == 0, "Initial stock already recorded for origin")
        inventory["on_hand"] += payload["quantity"]
    elif kind == "request":
        request_id = payload["request_id"]
        _require(request_id not in requests, "Request ID already exists")
        requests[request_id] = {
            "owner": origin, "quantity": payload["quantity"], "granted": 0, "received": 0,
        }
    elif kind == "grant":
        grant_id = payload["grant_id"]
        _require(grant_id not in grants, "Grant ID already exists")
        request = _record(requests, payload["request_id"])
        _require(origin == request["owner"], "Only request owner may grant")
        _require(payload["supplier"] != origin, "Supplier must differ from request owner")
        _require(request["granted"] + payload["quantity"] <= request["quantity"],
                 "Grants exceed request quantity")
        grants[grant_id] = {
            "request_id": payload["request_id"], "supplier": payload["supplier"],
            "quantity": payload["quantity"], "status": "granted",
        }
        request["granted"] += payload["quantity"]
    elif kind in ("accept", "approve", "dispatch"):
        if kind == "dispatch":
            _require(payload["dispatch_id"] not in dispatches, "Dispatch ID already exists")
        grant = _record(grants, payload["grant_id"])
        _require(origin == grant["supplier"], "Only supplier may accept, approve or dispatch")
        expected_status = {"accept": "granted", "approve": "accepted", "dispatch": "approved"}[kind]
        _require(grant["status"] == expected_status, f"{kind} requires {expected_status} grant")
        if kind == "accept":
            inventory = stocks.setdefault(origin, {"on_hand": 0, "reserved": 0})
            _require(grant["quantity"] <= inventory["on_hand"] - inventory["reserved"],
                     "Insufficient available stock")
            inventory["reserved"] += grant["quantity"]
            grant["status"] = "accepted"
        elif kind == "approve":
            grant["status"] = "approved"
        else:
            _require(payload["quantity"] == grant["quantity"], "Dispatch must equal full grant quantity")
            request = _record(requests, grant["request_id"])
            inventory = stocks.get(origin, {"on_hand": 0, "reserved": 0})
            _require(inventory["reserved"] >= grant["quantity"]
                     and inventory["on_hand"] >= grant["quantity"], "Insufficient reserved stock")
            inventory["on_hand"] -= grant["quantity"]
            inventory["reserved"] -= grant["quantity"]
            grant["status"] = "dispatched"
            dispatches[payload["dispatch_id"]] = {
                "grant_id": payload["grant_id"], "supplier": origin,
                "destination": request["owner"], "quantity": grant["quantity"], "received": 0,
            }
    else:  # receipt
        receipt_id = payload["receipt_id"]
        _require(receipt_id not in receipts, "Receipt ID already exists")
        dispatch = _record(dispatches, payload["dispatch_id"])
        _require(origin == dispatch["destination"], "Only destination may receive")
        _require(dispatch["received"] + payload["quantity"] <= dispatch["quantity"],
                 "Receipts exceed dispatched quantity")
        grant = _record(grants, dispatch["grant_id"])
        request = _record(requests, grant["request_id"])
        inventory = stocks.setdefault(origin, {"on_hand": 0, "reserved": 0})
        inventory["on_hand"] += payload["quantity"]
        dispatch["received"] += payload["quantity"]
        request["received"] += payload["quantity"]
        receipts[receipt_id] = {
            "dispatch_id": payload["dispatch_id"], "quantity": payload["quantity"], "origin": origin,
        }
    return result
