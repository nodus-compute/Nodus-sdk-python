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
