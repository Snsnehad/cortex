"""Storage backends (TASK-04.01).

One **contract suite** runs against every backend: ``LocalStorage`` in a temporary directory
and ``S3Storage`` against moto's S3 server over real HTTP (no AWS account, no Docker). Then
backend-specific tests: path confinement for local; encryption, presigned downloads and
error mapping for S3.
"""

import hashlib
import os
import socket
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3
import httpx
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from moto.server import ThreadedMotoServer

from app.config.settings import StorageSettings
from app.core.errors import PayloadTooLargeError
from app.storage import (
    InvalidStorageKeyError,
    ObjectNotFoundError,
    StorageBackend,
    StorageUnavailableError,
    create_storage,
    document_key,
    read_all,
    validate_key,
)
from app.storage.base import content_disposition
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage, create_s3_client

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

pytestmark = pytest.mark.anyio

KEY = "orgs/a/kbs/b/docs/c/original"
CONTENT = b"Cortex stores this file.\n" * 100


# --- fixtures -------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture(scope="session")
def moto_endpoint() -> Iterator[str]:
    port = _free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture
def aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fake credentials for moto; never real ones (and never the developer's profile).
    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def s3_settings(moto_endpoint: str, aws_env: None) -> StorageSettings:
    bucket = f"cortex-test-{uuid.uuid4().hex[:12]}"  # a bucket per test: no shared state
    settings = StorageSettings(
        backend="s3", s3_bucket=bucket, s3_region="us-east-1", s3_endpoint_url=moto_endpoint
    )
    create_s3_client(settings).create_bucket(Bucket=bucket)
    return settings


@pytest.fixture
def s3_client(s3_settings: StorageSettings) -> "S3Client":
    return create_s3_client(s3_settings)


@pytest.fixture
def s3_storage(s3_settings: StorageSettings) -> StorageBackend:
    return create_storage(s3_settings)


@pytest.fixture
def local_storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


@pytest.fixture(params=["local", "s3"])
def storage(request: pytest.FixtureRequest) -> StorageBackend:
    backend: StorageBackend = request.getfixturevalue(f"{request.param}_storage")
    return backend


async def stream(data: bytes, size: int = 64 * 1024) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


async def failing_stream() -> AsyncIterator[bytes]:
    yield b"first part"
    raise RuntimeError("client disconnected")


def leftovers(storage: StorageBackend) -> list[str]:
    """Temporary files left in a local root (S3 has none to leave)."""
    if not isinstance(storage, LocalStorage):
        return []
    return [p.name for p in storage.root.rglob("*") if p.name.endswith(".tmp")]


# --- keys -----------------------------------------------------------------------------------

BAD_KEYS = [
    "",
    "../escape",
    "orgs/../../escape",
    "/absolute/path",
    "orgs//double",
    "orgs/",
    "orgs\\windows",
    "C:/windows/system32",
    "C:",
    "orgs/.hidden",
    "orgs/trailing.",
    "orgs/with space",
    "orgs/naïve",
    "orgs/%2e%2e/x",
    "orgs/x\x00y",
    "a" * 501,
]


@pytest.mark.parametrize("key", BAD_KEYS)
def test_invalid_keys_are_rejected(key: str) -> None:
    with pytest.raises(InvalidStorageKeyError):
        validate_key(key)


def test_document_key_is_built_from_ids_only() -> None:
    org, kb, doc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert document_key(org, kb, doc) == f"orgs/{org}/kbs/{kb}/docs/{doc}/original"
    assert validate_key("a" * 500)  # the documents.storage_key column limit


# --- contract: every backend ----------------------------------------------------------------


async def test_put_returns_size_and_hash_and_reads_back(storage: StorageBackend) -> None:
    stored = await storage.put(KEY, CONTENT, content_type="text/plain")

    assert (stored.key, stored.size_bytes) == (KEY, len(CONTENT))
    assert stored.sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert await read_all(storage, KEY) == CONTENT
    assert await storage.exists(KEY)


async def test_streamed_upload_of_many_chunks(storage: StorageBackend) -> None:
    data = os.urandom(3 * 1024 * 1024 + 17)

    stored = await storage.put(KEY, stream(data), content_type="application/octet-stream")

    assert stored.size_bytes == len(data)
    assert stored.sha256 == hashlib.sha256(data).hexdigest()
    assert await read_all(storage, KEY) == data


async def test_reads_stream_in_bounded_chunks(storage: StorageBackend) -> None:
    await storage.put(KEY, CONTENT, content_type="text/plain")

    chunks = [chunk async for chunk in storage.iter_chunks(KEY, chunk_size=100)]

    assert all(0 < len(chunk) <= 100 for chunk in chunks)
    assert b"".join(chunks) == CONTENT


async def test_empty_file(storage: StorageBackend) -> None:
    stored = await storage.put(KEY, b"", content_type="text/plain")

    assert stored.size_bytes == 0
    assert stored.sha256 == hashlib.sha256(b"").hexdigest()
    assert await read_all(storage, KEY) == b""


async def test_put_replaces_an_existing_object(storage: StorageBackend) -> None:
    await storage.put(KEY, b"version 1", content_type="text/plain")
    await storage.put(KEY, b"version 2", content_type="text/plain")

    assert await read_all(storage, KEY) == b"version 2"


async def test_size_limit_is_inclusive(storage: StorageBackend) -> None:
    stored = await storage.put(KEY, stream(CONTENT, 7), content_type="t/p", max_bytes=len(CONTENT))
    assert stored.size_bytes == len(CONTENT)


async def test_oversized_upload_leaves_nothing_and_keeps_the_old_object(
    storage: StorageBackend,
) -> None:
    await storage.put(KEY, b"keep me", content_type="text/plain")
    new_key = "orgs/a/kbs/b/docs/new/original"

    for key in (KEY, new_key):
        with pytest.raises(PayloadTooLargeError) as caught:
            await storage.put(
                key, stream(CONTENT, 7), content_type="text/plain", max_bytes=len(CONTENT) - 1
            )
        assert caught.value.details == {"max_bytes": len(CONTENT) - 1}

    assert await read_all(storage, KEY) == b"keep me"
    assert not await storage.exists(new_key)
    assert leftovers(storage) == []


async def test_a_failing_upload_stream_leaves_nothing(storage: StorageBackend) -> None:
    with pytest.raises(RuntimeError, match="client disconnected"):
        await storage.put(KEY, failing_stream(), content_type="text/plain")

    assert not await storage.exists(KEY)
    assert leftovers(storage) == []


async def test_missing_objects(storage: StorageBackend) -> None:
    assert not await storage.exists(KEY)
    with pytest.raises(ObjectNotFoundError):
        await read_all(storage, KEY)
    await storage.delete(KEY)  # idempotent: no error


async def test_delete_removes_the_object(storage: StorageBackend) -> None:
    await storage.put(KEY, CONTENT, content_type="text/plain")

    await storage.delete(KEY)

    assert not await storage.exists(KEY)
    with pytest.raises(ObjectNotFoundError):
        await read_all(storage, KEY)


async def test_read_all_enforces_its_own_limit(storage: StorageBackend) -> None:
    await storage.put(KEY, CONTENT, content_type="text/plain")
    with pytest.raises(PayloadTooLargeError):
        await read_all(storage, KEY, max_bytes=len(CONTENT) - 1)


@pytest.mark.parametrize("key", ["../escape", "/abs", "a\\b", "orgs/.hidden"])
async def test_every_operation_validates_the_key(storage: StorageBackend, key: str) -> None:
    with pytest.raises(InvalidStorageKeyError):
        await storage.put(key, b"x", content_type="text/plain")
    with pytest.raises(InvalidStorageKeyError):
        await read_all(storage, key)
    with pytest.raises(InvalidStorageKeyError):
        await storage.exists(key)
    with pytest.raises(InvalidStorageKeyError):
        await storage.delete(key)
    with pytest.raises(InvalidStorageKeyError):
        await storage.presigned_get_url(key, filename="x", expires_in_seconds=60)


# --- local backend --------------------------------------------------------------------------


async def test_local_files_live_under_the_root(local_storage: LocalStorage) -> None:
    await local_storage.put(KEY, CONTENT, content_type="text/plain")

    path = local_storage.root.joinpath(*KEY.split("/"))
    assert path.read_bytes() == CONTENT
    assert local_storage.path_for(KEY) == path


@pytest.mark.parametrize("key", ["../escape", "orgs/../../escape", "/etc/passwd", "."])
def test_local_confinement_holds_even_without_key_validation(
    local_storage: LocalStorage, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    # Two independent walls: switch the first off (shared key validation) and the second
    # (the resolved path must stay inside the root) must still refuse every escape.
    monkeypatch.setattr("app.storage.local.validate_key", lambda key: key)
    with pytest.raises(InvalidStorageKeyError, match="escapes"):
        local_storage.path_for(key)


@pytest.mark.parametrize("key", ["orgs/con", "orgs/NUL.txt", "com1/x", "orgs/lpt9.pdf"])
def test_local_refuses_windows_device_names(local_storage: LocalStorage, key: str) -> None:
    with pytest.raises(InvalidStorageKeyError):
        local_storage.path_for(key)


async def test_local_refuses_a_symlink_that_leads_out_of_the_root(
    local_storage: LocalStorage, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = local_storage.root / "orgs"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:  # Windows without Developer Mode cannot create symlinks
        pytest.skip("symlinks are not available on this machine")

    with pytest.raises(InvalidStorageKeyError, match="escapes"):
        await local_storage.put(KEY, CONTENT, content_type="text/plain")
    with pytest.raises(InvalidStorageKeyError, match="escapes"):
        await read_all(local_storage, KEY)
    assert list(outside.iterdir()) == []


async def test_local_has_no_direct_urls(local_storage: LocalStorage) -> None:
    await local_storage.put(KEY, CONTENT, content_type="text/plain")
    assert await local_storage.presigned_get_url(KEY, filename="a", expires_in_seconds=60) is None


def test_factory_builds_the_configured_backend(tmp_path: Path) -> None:
    local = create_storage(StorageSettings(backend="local", local_root=tmp_path / "files"))
    assert isinstance(local, LocalStorage)
    assert (tmp_path / "files").is_dir()


# --- S3 backend -----------------------------------------------------------------------------


async def test_s3_objects_are_encrypted_typed_and_carry_their_hash(
    s3_storage: StorageBackend, s3_client: "S3Client", s3_settings: StorageSettings
) -> None:
    assert isinstance(s3_storage, S3Storage)
    await s3_storage.put(KEY, CONTENT, content_type="text/markdown")

    head = s3_client.head_object(Bucket=s3_settings.s3_bucket or "", Key=KEY)
    assert head["ServerSideEncryption"] == "AES256"
    assert head["ContentType"] == "text/markdown"
    assert head["Metadata"] == {"sha256": hashlib.sha256(CONTENT).hexdigest()}


async def test_s3_oversized_upload_never_reaches_the_bucket(
    s3_storage: StorageBackend, s3_client: "S3Client", s3_settings: StorageSettings
) -> None:
    with pytest.raises(PayloadTooLargeError):
        await s3_storage.put(KEY, stream(CONTENT), content_type="t/p", max_bytes=10)

    listing = s3_client.list_objects_v2(Bucket=s3_settings.s3_bucket or "")
    assert listing.get("KeyCount") == 0


async def test_s3_presigned_download_forces_an_attachment(s3_storage: StorageBackend) -> None:
    await s3_storage.put(KEY, CONTENT, content_type="text/html")

    url = await s3_storage.presigned_get_url(
        KEY, filename='Q3 "report" é.html', expires_in_seconds=60
    )
    assert url is not None
    async with httpx.AsyncClient() as client:
        response = await client.get(url)

    assert response.status_code == 200
    assert response.content == CONTENT
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''Q3%20%22report%22%20%C3%A9.html" in disposition
    assert "X-Amz-Signature" in url
    assert "X-Amz-Expires=60" in url


async def test_s3_presigned_url_is_useless_after_tampering(s3_storage: StorageBackend) -> None:
    await s3_storage.put(KEY, CONTENT, content_type="text/plain")
    url = await s3_storage.presigned_get_url(KEY, filename="a.txt", expires_in_seconds=60)
    assert url is not None

    other = url.replace("/docs/c/", "/docs/d/")
    async with httpx.AsyncClient() as client:
        response = await client.get(other)

    assert response.status_code == 403


async def test_s3_kms_settings_are_sent_with_every_upload() -> None:
    calls: list[dict[str, Any]] = []

    class RecordingClient:
        def upload_fileobj(self, fileobj: Any, bucket: str, key: str, ExtraArgs: Any) -> None:  # noqa: N803
            calls.append({"bucket": bucket, "key": key, **ExtraArgs})

    storage = S3Storage(
        RecordingClient(),  # type: ignore[arg-type]
        bucket="b",
        sse="aws:kms",
        kms_key_id="alias/cortex-documents",
    )
    await storage.put(KEY, b"x", content_type="text/plain")

    assert calls[0]["ServerSideEncryption"] == "aws:kms"
    assert calls[0]["SSEKMSKeyId"] == "alias/cortex-documents"


def test_kms_key_requires_kms_encryption() -> None:
    with pytest.raises(ValueError, match="s3_kms_key_id requires"):
        StorageSettings(backend="s3", s3_bucket="b", s3_kms_key_id="alias/x")


async def test_s3_unreachable_is_a_retryable_503(aws_env: None) -> None:
    # One attempt: the production client retries 3 times, and Windows takes ~2 s to refuse
    # each connection, which only slows the test down. The error mapping is what is tested.
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url="http://127.0.0.1:1",
        config=Config(retries={"max_attempts": 1, "mode": "standard"}, connect_timeout=1),
    )
    storage = S3Storage(client, bucket="b")

    with pytest.raises(StorageUnavailableError) as caught:
        await storage.exists(KEY)
    assert caught.value.code == "STORAGE_UNAVAILABLE"
    with pytest.raises(StorageUnavailableError):
        await storage.put(KEY, b"x", content_type="text/plain")
    with pytest.raises(StorageUnavailableError):
        await read_all(storage, KEY)
    with pytest.raises(StorageUnavailableError):
        await storage.delete(KEY)


def stubbed_storage() -> tuple[S3Storage, Stubber]:
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        config=Config(retries={"max_attempts": 1, "mode": "standard"}),
    )
    return S3Storage(client, bucket="b"), Stubber(client)


@pytest.mark.parametrize(("code", "status"), [("SlowDown", 503), ("InternalError", 500)])
async def test_s3_throttling_and_server_errors_are_retryable(code: str, status: int) -> None:
    storage, stubber = stubbed_storage()
    stubber.add_client_error("head_object", service_error_code=code, http_status_code=status)
    with stubber, pytest.raises(StorageUnavailableError):
        await storage.exists(KEY)


async def test_s3_configuration_errors_are_not_disguised_as_outages() -> None:
    storage, stubber = stubbed_storage()
    stubber.add_client_error("head_object", service_error_code="AccessDenied", http_status_code=403)
    with stubber, pytest.raises(ClientError) as caught:
        await storage.exists(KEY)
    assert not isinstance(caught.value, StorageUnavailableError)
    assert "AccessDenied" in str(caught.value)


# --- download names -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("report.pdf", "attachment; filename=\"report.pdf\"; filename*=UTF-8''report.pdf"),
        (
            'a"b\\c.txt',
            "attachment; filename=\"a_b_c.txt\"; filename*=UTF-8''a%22b%5Cc.txt",
        ),
        (
            "evil\r\nSet-Cookie: x=1.txt",
            'attachment; filename="evilSet-Cookie: x=1.txt"; '
            "filename*=UTF-8''evilSet-Cookie%3A%20x%3D1.txt",
        ),
        (
            "résumé.pdf",
            "attachment; filename=\"resume.pdf\"; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf",
        ),
        ("\r\n", "attachment; filename=\"download\"; filename*=UTF-8''download"),
    ],
)
def test_content_disposition_is_safe(filename: str, expected: str) -> None:
    assert content_disposition(filename) == expected
