"""Mount handle_event behind the customer's existing HTTPS webhook endpoint."""
import hashlib
import hmac
import json
import time


def handle_event(db, body, timestamp, signature, secret):
    if not secret or len(body) > 1_048_576:
        raise ValueError("Invalid webhook")
    try:
        valid_time = abs(time.time() - int(timestamp)) <= 300
    except (ValueError, TypeError):
        valid_time = False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    if not valid_time or not hmac.compare_digest(expected, signature):
        raise ValueError("Invalid webhook")
    event = json.loads(body)
    workload_id = event.get("workload_id")
    if event.get("event_type") not in {"workload.completed", "workload.failed", "workload.cancelled"}:
        return None
    with db:
        db.execute("INSERT OR IGNORE INTO completions(workload,event_type) VALUES (?,?)", (workload_id, event["event_type"]))
        db.execute("UPDATE batches SET status=? WHERE workload=?", (event["event_type"], workload_id))
    return workload_id
