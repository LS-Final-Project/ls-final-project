"""원문 저장소(KAN-188). Cloud Storage는 합성 전송으로 대체한다."""

import asyncio
import base64
import hashlib

import httpx
import pytest

from backend.api.tft.rfp import storage


async def token() -> str:
    return "synthetic"


def gcs(handler) -> storage.GcsDocumentStore:
    return storage.GcsDocumentStore(
        "team4-rfp", token=token, transport=httpx.MockTransport(handler)
    )


def test_gcs_creates_only_new_objects_with_md5():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={})

    asyncio.run(gcs(handler).put("rfp/p/v", b"data"))
    [request] = seen
    md5 = base64.b64encode(hashlib.md5(b"data", usedforsecurity=False).digest()).decode()
    assert request.url.params["ifGenerationMatch"] == "0"
    assert request.url.params["name"] == "rfp/p/v"
    assert request.headers["x-goog-hash"] == f"md5={md5}"
    assert request.headers["authorization"] == "Bearer synthetic"


def test_gcs_failures_do_not_name_the_bucket_or_key():
    def refuse(request):
        return httpx.Response(412, text="team4-rfp rfp/p/v exists")

    def broken(request):
        raise httpx.ConnectError("team4-rfp unreachable")

    for handler in (refuse, broken):
        store = gcs(handler)
        with pytest.raises(storage.DocumentUnavailable) as caught:
            asyncio.run(store.put("rfp/p/v", b"data"))
        assert "team4-rfp" not in str(caught.value)
        with pytest.raises(storage.DocumentUnavailable):
            asyncio.run(store.get("rfp/p/v"))


def test_gcs_reads_the_object_by_encoded_name():
    def handler(request):
        assert request.url.raw_path.startswith(b"/storage/v1/b/team4-rfp/o/rfp%2Fp%2Fv")
        assert request.url.params["alt"] == "media"
        return httpx.Response(200, content=b"data")

    assert asyncio.run(gcs(handler).get("rfp/p/v")) == b"data"


def test_bucket_wins_and_nothing_configured_means_no_store(tmp_path):
    both = storage.DocumentStorageSettings(
        _env_file=None, document_root=tmp_path, document_bucket="team4-rfp"
    )
    assert isinstance(storage.document_store(both), storage.GcsDocumentStore)
    only_root = storage.DocumentStorageSettings(_env_file=None, document_root=tmp_path)
    assert isinstance(storage.document_store(only_root), storage.LocalDocumentStore)
    nothing = storage.DocumentStorageSettings(_env_file=None)
    assert storage.document_store(nothing) is None


def test_local_store_stays_inside_the_folder_and_never_overwrites(tmp_path):
    store = storage.LocalDocumentStore(tmp_path)
    asyncio.run(store.put("rfp/p/v", b"data"))
    with pytest.raises(storage.DocumentUnavailable):
        asyncio.run(store.put("rfp/p/v", b"other"))
    assert asyncio.run(store.get("rfp/p/v")) == b"data"
    with pytest.raises(storage.DocumentUnavailable):
        asyncio.run(store.put("../outside", b"data"))
    with pytest.raises(storage.DocumentUnavailable):
        asyncio.run(store.get("../outside"))


def test_read_document_checks_hash_and_size(tmp_path):
    store = storage.LocalDocumentStore(tmp_path)
    asyncio.run(store.put("k", b"data"))
    digest = hashlib.sha256(b"data").hexdigest()
    assert asyncio.run(storage.read_document(store, "k", digest, 4)) == b"data"
    with pytest.raises(storage.DocumentUnavailable):
        asyncio.run(storage.read_document(store, "k", "0" * 64, 4))
