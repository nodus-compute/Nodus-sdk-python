import hashlib
import hmac
import importlib.util
import json
import time
from pathlib import Path

import httpx
import nodus
import pytest


def load(name):
    path = Path(__file__).parents[1] / "examples" / "microbatch" / (name + ".py")
    spec = importlib.util.spec_from_file_location("microbatch_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_submission_unknown_outcome_reuses_exact_batch(tmp_path):
    queue = load("queue")
    db = queue.connect(tmp_path / "queue.sqlite")
    queue.enqueue(db, "r1", 0.5)
    sent = []

    def handler(request):
        sent.append((request.headers["Idempotency-Key"], json.loads(request.content)))
        if len(sent) == 1:
            raise httpx.ReadTimeout("unknown response", request=request)
        return httpx.Response(202, json={"id": "wl_batch", "status": "queued"})

    with nodus.Client(api_key="test-key", base_url="https://nodus.test", max_retries=0) as client:
        client._http = httpx.Client(base_url="https://nodus.test", transport=httpx.MockTransport(handler))
        with pytest.raises(nodus.APITimeoutError):
            queue.submit(db, client, 2.0, "image:qualified")
        queue.enqueue(db, "r2", 0.9)
        assert queue.submit(db, client, 99.0, "different:image") == "wl_batch"
    assert sent[0] == sent[1]
    assert db.execute("SELECT batch FROM requests WHERE id='r2'").fetchone() == (None,)
    with pytest.raises(ValueError):
        queue.enqueue(db, "r1", 2.0)


def test_webhook_real_wire_and_forgery(tmp_path):
    queue, webhook = load("queue"), load("webhook")
    db = queue.connect(tmp_path / "queue.sqlite")
    db.execute("INSERT INTO batches(id,payload,workload) VALUES ('b','{}','wl_batch')")
    db.commit()
    body = json.dumps({"event_id": "ev1", "event_type": "workload.completed", "workload_id": "wl_batch", "payload": {}}).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(b"local-test", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    with pytest.raises(ValueError):
        webhook.handle_event(db, body, timestamp, "forged", "local-test")
    assert db.execute("SELECT status FROM batches").fetchone() == ("pending",)
    assert webhook.handle_event(db, body, timestamp, signature, "local-test") == "wl_batch"
    assert webhook.handle_event(db, body, timestamp, signature, "local-test") == "wl_batch"
    assert db.execute("SELECT status FROM batches").fetchone() == ("workload.completed",)


def test_webhook_before_submission_response_is_retained(tmp_path):
    queue, webhook = load("queue"), load("webhook")
    db=queue.connect(tmp_path / "queue.sqlite")
    queue.enqueue(db,"r1",0.5)
    timestamp=str(int(time.time()))
    body=json.dumps({'event_type':'workload.completed','workload_id':'wl_fast'}).encode()
    signature=hmac.new(b'local-test',timestamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
    def handler(request):
        webhook.handle_event(db,body,timestamp,signature,'local-test')
        return httpx.Response(202,json={'id':'wl_fast','status':'accepted'})
    with nodus.Client(api_key='test',base_url='https://nodus.test') as client:
        client._http.close()
        client._http=httpx.Client(base_url='https://nodus.test',transport=httpx.MockTransport(handler))
        queue.submit(db,client,2,'image:qualified')
    assert db.execute('SELECT status FROM batches').fetchone()==('workload.completed',)
