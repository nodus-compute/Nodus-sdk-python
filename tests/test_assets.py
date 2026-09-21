"""Asset requests match the authenticated import and upload handlers."""
import asyncio
import json

import httpx
import pytest

import nodus
from nodus._assets import Assets, AsyncAssets

ROW = {"id": "asset_123", "kind": "upload", "name": "train.py", "state": "ready",
       "revision": "", "sha256": "a" * 64, "stored_bytes": 1024,
       "imported_bytes": 8, "created_at": "2026-09-07T00:00:00Z",
       "ready_at": None, "deleted_at": None, "future": "retained"}


def exercise(handler, asynchronous, action):
    cls = nodus.AsyncClient if asynchronous else nodus.Client
    client = cls(api_key="nk_test", base_url="https://nodus.invalid", max_retries=2)
    http = httpx.AsyncClient if asynchronous else httpx.Client
    client._http = http(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler),
                        headers={"Authorization": "Bearer nk_test"})
    if asynchronous:
        async def run():
            async with client:
                return await action(AsyncAssets(client))
        return asyncio.run(run())
    with client:
        return action(Assets(client))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_asset_list_preserves_wire_fields(asynchronous):
    result = exercise(lambda req: httpx.Response(200, json={"assets": [ROW]}),
                      asynchronous, lambda assets: assets.list())
    assert result[0].id == "asset_123"
    assert result[0].state == "ready"
    assert result[0].stored_bytes == 1024
    assert result[0].raw["future"] == "retained"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("method,args,expected", [
    ("import_url", ("https://data.example/train.csv",),
     {"kind": "url", "url": "https://data.example/train.csv"}),
    ("import_github", ("owner/repo",), {"kind": "github", "repo": "owner/repo"}),
    ("import_huggingface", ("owner/data",), {"kind": "huggingface", "repo": "owner/data"}),
])
def test_import_wire(asynchronous, method, args, expected):
    def handler(req):
        assert req.headers["Authorization"] == "Bearer nk_test"
        assert req.url.path == "/v1/assets/import"
        assert json.loads(req.content) == expected
        return httpx.Response(201, json=ROW)
    result = exercise(handler, asynchronous, lambda assets: getattr(assets, method)(*args))
    assert result.id == "asset_123"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_upload_raw_file_and_limit(asynchronous, tmp_path):
    path = tmp_path / "train.py"
    path.write_bytes(b"print(1)")
    calls = []
    def handler(req):
        calls.append(req.method)
        if req.method == "GET":
            return httpx.Response(200, json={"assets": [], "max_import_bytes": 100})
        assert req.url.path == "/v1/assets/upload"
        assert req.url.params["name"] == "train.py"
        assert req.content == b"print(1)"
        return httpx.Response(201, json=ROW)
    assert exercise(handler, asynchronous, lambda assets: assets.upload(path)).id == "asset_123"
    assert calls == ["GET", "POST"]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_upload_refuses_oversize_before_post(asynchronous, tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"123")
    def handler(req):
        assert req.method == "GET"
        return httpx.Response(200, json={"assets": [], "max_import_bytes": 2})
    with pytest.raises(nodus.ValidationError, match="limit"):
        exercise(handler, asynchronous, lambda assets: assets.upload(path))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_delete_validates_id_before_http(asynchronous):
    def handler(req):
        pytest.fail("invalid ID reached network")
    with pytest.raises(nodus.ValidationError):
        exercise(handler, asynchronous, lambda assets: assets.delete("../other"))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_import_not_retried_on_ambiguous_failure(asynchronous):
    calls = []
    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("ambiguous result", request=req)
    with pytest.raises(nodus.APITimeoutError):
        exercise(handler, asynchronous, lambda assets: assets.import_github("owner/repo"))
    assert len(calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("status", [302, 503])
def test_import_never_follows_redirect_or_retries(asynchronous, status):
    calls = []
    def handler(req):
        calls.append(req.url.host)
        return httpx.Response(status, json={"error": "unavailable"},
                              headers={"Location": "https://untrusted.example"})
    with pytest.raises(nodus.NodusError):
        exercise(handler, asynchronous, lambda assets: assets.import_github("owner/repo"))
    assert calls == ["nodus.invalid"]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("method,value", [
    ("import_url", "http://data.example/file"),
    ("import_url", "https://user:secret@data.example/file"),
    ("import_url", "https://data.example/file\n"),
    ("import_github", "owner/../repo"),
    ("import_huggingface", "https://huggingface.co/owner/model"),
])
def test_invalid_sources_never_reach_network(asynchronous, method, value):
    def handler(req):
        pytest.fail("invalid source reached network")
    with pytest.raises(nodus.ValidationError):
        exercise(handler, asynchronous, lambda assets: getattr(assets, method)(value))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_import_token_and_revision_wire(asynchronous):
    def handler(req):
        assert json.loads(req.content) == {"kind": "huggingface", "repo": "owner/data",
            "ref": "release", "files": ["train/*.jsonl"], "token": "hf_secret"}
        return httpx.Response(201, json=ROW)
    result = exercise(handler, asynchronous, lambda assets: assets.import_huggingface(
        "owner/data", ref="release", files=["train/*.jsonl"], token="hf_secret"))
    assert "hf_secret" not in repr(result)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_delete_actual_route(asynchronous):
    def handler(req):
        assert req.method == "DELETE"
        assert req.url.path == "/v1/assets/asset_123"
        return httpx.Response(204)
    assert exercise(handler, asynchronous, lambda assets: assets.delete("asset_123")) is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_missing_upload_limit_fails_closed(asynchronous, tmp_path):
    file = tmp_path / "sample"
    file.write_bytes(b"a")
    def handler(req):
        assert req.method == "GET"
        return httpx.Response(200, json={"assets": []})
    with pytest.raises(nodus.APIError, match="limit"):
        exercise(handler, asynchronous, lambda assets: assets.upload(file))
def test_asset_in_use_is_not_an_idempotency_error():
    from nodus.errors import error_from_response
    error = error_from_response("DELETE", "/v1/assets/asset_data", 409,
                                {"error": "asset_in_use", "message": "asset is used by an active workload"})
    assert isinstance(error, nodus.APIError)
    assert not isinstance(error, nodus.IdempotencyConflictError)
    assert error.status_code == 409

@pytest.mark.parametrize("asynchronous", [False, True])
def test_import_query_wire_and_export_metadata(asynchronous):
    def handler(req):
        assert req.url.path == "/v1/assets/import"
        assert json.loads(req.content) == {"kind": "connection_query", "connection_id": "lab-db", "sql": "SELECT 'a  b'", "format": "csv", "branch": "main", "reuse": True}
        return httpx.Response(201, json={**ROW, "export": {"row_count": 10, "format": "csv"}})
    result = exercise(handler, asynchronous, lambda assets: assets.import_query("lab-db", "SELECT 'a  b'", format="csv", branch="main", reuse=True))
    assert result.export == {"row_count": 10, "format": "csv"}

@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("sql", ["UPDATE clips SET id=1", "SELECTED 1", "SELECT_1", "SELECT1", "WITH$bad$", ""])
def test_import_query_validation_before_http(asynchronous, sql):
    def handler(req):
        pytest.fail("invalid query reached HTTP")
    with pytest.raises(nodus.ValidationError):
        exercise(handler, asynchronous, lambda assets: assets.import_query("lab-db", sql))


def test_asset_import_query_cli_wire(monkeypatch, capsys):
    from nodus import cli
    from test_sandbox_cli import client_factory
    def handler(req):
        assert json.loads(req.content) == {"kind": "connection_query", "connection_id": "db", "sql": "SELECT 1", "format": "parquet", "reuse": True}
        return httpx.Response(201, json=ROW)
    monkeypatch.setattr(cli, "Client", client_factory(handler))
    assert cli.main(["asset", "import-query", "db", "SELECT 1", "--reuse"]) == 0
    assert "asset_123" in capsys.readouterr().out

@pytest.mark.parametrize("asynchronous", [False, True])
def test_import_query_polls_durable_asset_until_ready(asynchronous, monkeypatch):
    import nodus._assets as module
    monkeypatch.setattr(module, "_QUERY_POLL_SECONDS", 0.001, raising=False)
    calls = []
    def handler(req):
        calls.append(req.method)
        if req.method == "POST":
            return httpx.Response(202, json={**ROW, "kind": "connection_query", "state": "importing"})
        assert req.url.path == "/v1/assets/asset_123"
        return httpx.Response(200, json={**ROW, "kind": "connection_query", "state": "ready"})
    result = exercise(handler, asynchronous, lambda assets: assets.import_query("db", "SELECT 1"))
    assert result.state == "ready"
    assert calls == ["POST", "GET"]

@pytest.mark.parametrize("asynchronous", [False, True])
def test_import_query_failed_asset_raises_safe_failure(asynchronous, monkeypatch):
    import nodus._assets as module
    monkeypatch.setattr(module, "_QUERY_POLL_SECONDS", 0.001, raising=False)
    def handler(req):
        return httpx.Response(202 if req.method == "POST" else 200, json={**ROW, "state": "importing" if req.method == "POST" else "failed", "error": "database query failed at 2 rows"})
    with pytest.raises(nodus.APIError, match="asset_123"):
        exercise(handler, asynchronous, lambda assets: assets.import_query("db", "SELECT 1"))

@pytest.mark.parametrize("asynchronous", [False, True])
def test_import_query_polling_has_overall_deadline(asynchronous, monkeypatch):
    from types import SimpleNamespace
    import nodus._assets as module

    # A coarse clock can return the same tick before and after a short sleep.
    # Keep this clock local to the asset module so asyncio retains its real clock.
    clock = SimpleNamespace(now=4096.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(
        monotonic=lambda: clock.now, sleep=lambda delay: None))
    async def sleep(delay):
        pass
    monkeypatch.setattr(module, "asyncio", SimpleNamespace(
        sleep=sleep, CancelledError=asyncio.CancelledError))
    monkeypatch.setattr(module, "_QUERY_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(module, "_QUERY_POLL_SECONDS", 0.002)
    calls, timeouts = [], []
    def handler(req):
        calls.append(req.method)
        if req.method == "GET":
            timeout = req.extensions["timeout"]["read"]
            timeouts.append(timeout)
            clock.now += 0.006
        return httpx.Response(202 if req.method == "POST" else 200, json={**ROW, "state": "importing"})
    with pytest.raises(nodus.APITimeoutError, match="asset_123"):
        exercise(handler, asynchronous, lambda assets: assets.import_query("db", "SELECT 1"))
    assert calls == ["POST", "GET", "GET"]
    assert timeouts == pytest.approx([0.01, 0.004], rel=0, abs=1e-9)

@pytest.mark.parametrize("asynchronous", [False, True])
def test_query_poll_transport_failure_keeps_admitted_asset_identity(asynchronous, monkeypatch):
    import nodus._assets as module
    monkeypatch.setattr(module, "_QUERY_POLL_SECONDS", 0.001)
    calls = []
    def handler(req):
        calls.append(req.method)
        if req.method == "POST":
            return httpx.Response(202, json={**ROW, "state": "importing"})
        raise httpx.ReadTimeout("private transport detail", request=req)
    with pytest.raises(nodus.APITimeoutError, match="asset_123") as failure:
        exercise(handler, asynchronous, lambda assets: assets.import_query("db", "SELECT 1"))
    assert "private" not in str(failure.value)
    assert calls == ["POST", "GET"]

@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("failure", ["connection", 503])
def test_query_observation_failure_has_structured_asset_identity(asynchronous, failure, monkeypatch):
    import nodus._assets as module
    monkeypatch.setattr(module, "_QUERY_POLL_SECONDS", 0.001)
    def handler(req):
        if req.method == "POST":
            return httpx.Response(202, json={**ROW, "state": "importing"})
        if failure == "connection":
            raise httpx.ConnectError("private-network-detail", request=req)
        return httpx.Response(failure, json={"error": "unavailable", "message": "private-remote-detail"})
    with pytest.raises(nodus.NodusError) as raised:
        exercise(handler, asynchronous, lambda assets: assets.import_query("db", "SELECT 1"))
    assert raised.value.asset_id == "asset_123"

@pytest.mark.parametrize("failure", ["connection", 503, "interrupt"])
@pytest.mark.parametrize("debug", [False, True])
def test_query_cli_retains_safe_admitted_identity(failure, debug, monkeypatch, capsys):
    import nodus._assets as module
    from nodus import cli
    from test_sandbox_cli import client_factory
    monkeypatch.setattr(module, "_QUERY_POLL_SECONDS", 0.001)
    calls = []
    def handler(req):
        calls.append(req.method)
        if req.method == "POST":
            return httpx.Response(202, json={**ROW, "state": "importing"})
        if failure == "connection":
            raise httpx.ConnectError("private-network-detail", request=req)
        if failure == "interrupt":
            raise KeyboardInterrupt
        return httpx.Response(failure, json={"error": "unavailable", "message": "private-remote-detail"})
    monkeypatch.setattr(cli, "Client", client_factory(handler))
    result = cli.main((["--debug"] if debug else []) + ["asset", "import-query", "db", "SELECT 1"])
    output = capsys.readouterr()
    assert result == (130 if failure == "interrupt" else 2)
    assert "asset_123" in output.err and "before repeating" in output.err
    assert "private-" not in output.err
    assert calls == ["POST", "GET"]

@pytest.mark.parametrize("asynchronous", [False, True])
def test_query_observation_cancellation_preserves_identity(asynchronous, monkeypatch):
    import nodus._assets as module
    monkeypatch.setattr(module, "_QUERY_POLL_SECONDS", 0.001)
    interruption = asyncio.CancelledError if asynchronous else KeyboardInterrupt
    def handler(req):
        if req.method == "POST":
            return httpx.Response(202, json={**ROW, "state": "importing"})
        raise interruption
    with pytest.raises(interruption) as raised:
        exercise(handler, asynchronous, lambda assets: assets.import_query("db", "SELECT 1"))
    assert nodus.asset_id_from_error(raised.value) == "asset_123"
    if not asynchronous:
        assert raised.value.asset_id == "asset_123"


def test_query_cli_recovers_failed_export_by_exact_id(monkeypatch, capsys):
    import nodus._assets as module
    from nodus import cli
    from test_sandbox_cli import client_factory
    monkeypatch.setattr(module, "_QUERY_POLL_SECONDS", 0.001)
    calls = []
    def handler(req):
        calls.append((req.method, req.url.path))
        if req.method == "POST":
            return httpx.Response(202, json={**ROW, "state": "importing"})
        if len(calls) == 2:
            return httpx.Response(503, json={"error": "unavailable", "message": "private-response-detail"})
        return httpx.Response(200, json={**ROW, "state": "failed", "error": "query cancelled or timed out at 5 rows", "export": {"format": "parquet", "row_count": 5}, "private_future": "not-displayable"})
    monkeypatch.setattr(cli, "Client", client_factory(handler))
    assert cli.main(["asset", "import-query", "db", "SELECT 1"]) == 2
    recovery = capsys.readouterr().err
    assert "nodus asset get asset_123" in recovery
    assert "private-response-detail" not in recovery
    assert cli.main(["asset", "get", "asset_123"]) == 0
    detail = capsys.readouterr().out
    for value in ["asset_123", "failed", "query cancelled or timed out at 5 rows", "parquet"]:
        assert value in detail
    assert "not-displayable" not in detail
    assert calls == [("POST", "/v1/assets/import"), ("GET", "/v1/assets/asset_123"), ("GET", "/v1/assets/asset_123")]


def test_query_cancellation_identity_survives_task_boundary(monkeypatch):
    import nodus._assets as module
    monkeypatch.setattr(module, "_QUERY_POLL_SECONDS", 0.001)
    async def run():
        observing = asyncio.Event()
        calls = []
        async def handler(req):
            calls.append(req.method)
            if req.method == "POST":
                return httpx.Response(202, json={**ROW, "state": "importing"})
            observing.set()
            await asyncio.Event().wait()
        async with nodus.AsyncClient(api_key="nk_test", base_url="https://nodus.invalid") as client:
            await client._http.aclose()
            client._http = httpx.AsyncClient(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
            async def observe():
                try:
                    return await client.assets.import_query("db", "SELECT 1")
                except asyncio.CancelledError as exc:
                    assert exc.asset_id == "asset_123"
                    raise
            task = asyncio.create_task(observe())
            await observing.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError) as raised:
                await task
            assert task.cancelled()
            assert nodus.asset_id_from_error(raised.value) == "asset_123"
            assert calls == ["POST", "GET"]
    asyncio.run(run())


def test_asset_identity_accessor_uses_validated_metadata_without_error_text():
    original = asyncio.CancelledError("private transport detail")
    original.asset_id = "asset_admitted"
    wrapper = asyncio.CancelledError()
    wrapper.__context__ = original
    assert nodus.asset_id_from_error(wrapper) == "asset_admitted"
    original.asset_id = "asset_admitted\nforged output"
    original.__cause__ = wrapper
    assert nodus.asset_id_from_error(wrapper) is None
    assert nodus.asset_id_from_error(nodus.APIError("asset_from_untrusted_text")) is None
