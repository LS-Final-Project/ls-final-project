"""KAN-72 Cloud Storage 메일 객체 저장소를 가짜 Cloud Storage 응답으로 검증한다.

실제 버킷에는 연결하지 않는다. 이 검사 통과가 운영 버킷 쓰기 확인을 뜻하지 않는다.
"""

import asyncio
import base64
import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from backend.api.platform.mail.storage import GcsObjectStore, GoogleAccessToken, content_key
from backend.shared.errors import ApplicationError

BUCKET = "proj-test-team4-mail-objects"
DATA = b"Subject: hi\r\n\r\nbody"


def md5_of(data):
    return base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()


def meta(key, data):
    return {"name": key, "size": str(len(data)), "md5Hash": md5_of(data)}


class FakeGcs:
    """정해 둔 상태로 업로드·메타데이터 요청에 답하는 가짜 Cloud Storage."""

    def __init__(self):
        self.objects = {}
        self.requests = []
        self.upload_status = None
        self.get_status = None

    def handler(self, request):
        self.requests.append(request)
        assert request.headers["authorization"] == "Bearer gcs-token"
        if request.method == "POST":
            name = request.url.params["name"]
            if self.upload_status is not None:
                return httpx.Response(self.upload_status, json={"error": {"code": 0}})
            if name in self.objects:
                return httpx.Response(412, json={"error": {"code": 412}})
            body = request.content
            self.objects[name] = body
            return httpx.Response(200, json=meta(name, body))
        name = request.url.raw_path.decode().split("?")[0].split("/o/", 1)[1]
        name = name.replace("%2F", "/")
        if self.get_status is not None:
            return httpx.Response(self.get_status, json={"error": {"code": 0}})
        if name not in self.objects:
            return httpx.Response(404, json={"error": {"code": 404}})
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=self.objects[name])
        return httpx.Response(200, json=meta(name, self.objects[name]))


async def token():
    return "gcs-token"


def put(gcs, data=DATA, mailbox_id=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(gcs.handler)) as client:
            return await GcsObjectStore(client, BUCKET, token).put(
                data, mailbox_id=mailbox_id or uuid4()
            )

    return asyncio.run(go())


def test_new_object_is_created_only_if_absent_with_checksum():
    gcs, mailbox_id = FakeGcs(), uuid4()
    stored = put(gcs, mailbox_id=mailbox_id)
    digest = hashlib.sha256(DATA).hexdigest()
    assert (stored.key, stored.sha256, stored.size) == (
        content_key(mailbox_id, digest),
        digest,
        len(DATA),
    )
    [request] = gcs.requests
    assert request.url.host == "storage.googleapis.com"
    assert request.url.path == f"/upload/storage/v1/b/{BUCKET}/o"
    assert dict(request.url.params) == {
        "uploadType": "media",
        "name": stored.key,
        "ifGenerationMatch": "0",
    }
    assert request.headers["x-goog-hash"] == f"md5={md5_of(DATA)}"
    assert request.content == DATA


@pytest.mark.parametrize("refusal", [412, 403])
def test_existing_identical_object_is_reused_without_overwrite(refusal):
    gcs, mailbox_id = FakeGcs(), uuid4()
    first = put(gcs, mailbox_id=mailbox_id)
    gcs.requests.clear()
    if refusal == 403:
        # 덮어쓰기 권한이 없어 거부되는 경우도 같은 객체가 있으면 성공이다.
        gcs.upload_status = 403
    assert put(gcs, mailbox_id=mailbox_id) == first
    assert [r.method for r in gcs.requests] == ["POST", "GET"]
    assert b"%2F" in gcs.requests[1].url.raw_path


def test_same_content_in_another_mailbox_is_a_separate_object():
    gcs = FakeGcs()
    first, second = put(gcs), put(gcs)
    assert first.key != second.key
    assert sorted(gcs.objects) == sorted([first.key, second.key])


def test_existing_object_with_different_bytes_is_not_accepted():
    gcs, mailbox_id = FakeGcs(), uuid4()
    key = content_key(mailbox_id, hashlib.sha256(DATA).hexdigest())
    gcs.objects[key] = b"other bytes"
    with pytest.raises(ApplicationError) as error:
        put(gcs, mailbox_id=mailbox_id)
    assert error.value.code == "MAIL_STORAGE_UNAVAILABLE"


def test_upload_reply_that_does_not_match_is_not_accepted():
    gcs = FakeGcs()

    def short_reply(request):
        gcs.requests.append(request)
        return httpx.Response(200, json={"name": request.url.params["name"], "size": "1"})

    gcs.handler = short_reply
    with pytest.raises(ApplicationError) as error:
        put(gcs)
    assert error.value.code == "MAIL_STORAGE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("upload", "lookup"), [(403, 403), (403, 404), (412, 503), (500, None), (401, None)]
)
def test_storage_refusals_are_failures_not_success(upload, lookup):
    gcs = FakeGcs()
    gcs.upload_status, gcs.get_status = upload, lookup
    with pytest.raises(ApplicationError) as error:
        put(gcs)
    assert (error.value.status_code, error.value.code) == (503, "MAIL_STORAGE_UNAVAILABLE")
    assert BUCKET not in error.value.message


def test_connection_failure_is_a_storage_failure():
    def broken(request):
        raise httpx.ConnectError("down", request=request)

    gcs = FakeGcs()
    gcs.handler = broken
    with pytest.raises(ApplicationError) as error:
        put(gcs)
    assert error.value.code == "MAIL_STORAGE_UNAVAILABLE"


@pytest.mark.parametrize("bucket", ["", "UPPER", "a", "gs://bucket", "a..b", "bad/name", "-x-"])
def test_invalid_bucket_names_are_rejected(bucket):
    with pytest.raises(ValueError):
        GcsObjectStore(httpx.AsyncClient(), bucket, token)


class FakeCredentials:
    def __init__(self, valid, fail=False):
        self.valid, self.fail, self.refreshes = valid, fail, 0
        self.token = "cached" if valid else None

    def refresh(self, request):
        from google.auth.exceptions import RefreshError

        self.refreshes += 1
        if self.fail:
            raise RefreshError(json.dumps({"error": "secret detail"}))
        self.valid, self.token = True, "refreshed"


def test_google_token_refreshes_only_when_needed():
    valid = FakeCredentials(valid=True)
    assert asyncio.run(GoogleAccessToken(valid)()) == "cached"
    assert valid.refreshes == 0
    expired = FakeCredentials(valid=False)
    assert asyncio.run(GoogleAccessToken(expired)()) == "refreshed"
    assert expired.refreshes == 1


def test_google_token_failure_hides_provider_detail():
    with pytest.raises(ApplicationError) as error:
        asyncio.run(GoogleAccessToken(FakeCredentials(valid=False, fail=True))())
    assert error.value.code == "MAIL_STORAGE_UNAVAILABLE"
    assert "secret detail" not in error.value.message
    assert error.value.__cause__ is None


def test_stored_object_is_read_back_only_when_hash_and_size_match():
    gcs, mailbox_id = FakeGcs(), uuid4()
    stored = put(gcs, mailbox_id=mailbox_id)

    def media(request):
        assert request.url.params["alt"] == "media"
        name = request.url.raw_path.decode().split("?")[0].split("/o/", 1)[1]
        return httpx.Response(200, content=gcs.objects[name.replace("%2F", "/")])

    async def read(handler, target):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await GcsObjectStore(client, BUCKET, token).get(target)

    assert asyncio.run(read(media, stored)) == DATA
    gcs.objects[stored.key] = b"changed"
    with pytest.raises(ApplicationError):
        asyncio.run(read(media, stored))
    with pytest.raises(ApplicationError):
        asyncio.run(read(lambda r: httpx.Response(404), stored))


def test_local_store_reads_back_and_refuses_paths_outside_the_root(tmp_path):
    from backend.api.platform.mail.storage import LocalObjectStore, StoredObject

    store = LocalObjectStore(tmp_path / "root")
    stored = asyncio.run(store.put(DATA, mailbox_id=uuid4()))
    assert asyncio.run(store.get(stored)) == DATA
    (tmp_path / "secret").write_bytes(DATA)
    outside = StoredObject(key="../secret", sha256=stored.sha256, size=stored.size)
    with pytest.raises(ApplicationError):
        asyncio.run(store.get(outside))
    with pytest.raises(ApplicationError):
        asyncio.run(store.get(StoredObject(key=stored.key, sha256="0" * 64, size=stored.size)))
