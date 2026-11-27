"""Streaming multipart intake (TASK-04.03): limits enforced while the body arrives.

A test route on the real app (error envelope, middleware) calls ``receive_upload`` and
``detect_upload``. Streamed request bodies count how many chunks the server actually pulled,
to prove that oversized uploads are refused without being read.
"""

import hashlib
import tempfile
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request

from app.config import Settings
from app.main import create_app
from app.uploads import detect_upload, receive_upload

pytestmark = pytest.mark.anyio

MAX_BYTES = 1024 * 1024
BOUNDARY = "cortex-test-boundary"
TEXT = b"Hello, Cortex.\n" * 100


@pytest.fixture
def app() -> FastAPI:
    application = create_app(Settings(_env_file=None))

    @application.post("/api/v1/_test/upload")
    async def upload(request: Request) -> dict[str, Any]:
        received = await receive_upload(request, max_bytes=MAX_BYTES)
        try:
            detected = await detect_upload(received)
            content = received.file.read()
            return {
                "filename": received.filename,
                "declared_content_type": received.declared_content_type,
                "size_bytes": received.size_bytes,
                "sha256": received.sha256,
                "mime_type": detected.mime_type,
                "content_matches": hashlib.sha256(content).hexdigest() == received.sha256,
                "length_read": len(content),
            }
        finally:
            received.close()

    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def post_file(
    client: httpx.AsyncClient, content: bytes, filename: str = "notes.txt", **kw: Any
) -> httpx.Response:
    return await client.post(
        "/api/v1/_test/upload", files={"file": (filename, content, "text/plain")}, **kw
    )


def multipart(*parts: tuple[str, str | None, bytes], close: bool = True) -> bytes:
    """Hand-built multipart body: (field name, filename or None, content) per part."""
    body = b""
    for name, filename, content in parts:
        disposition = f'form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        body += (
            f"--{BOUNDARY}\r\nContent-Disposition: {disposition}\r\n\r\n".encode()
            + content
            + b"\r\n"
        )
    if close:
        body += f"--{BOUNDARY}--\r\n".encode()
    return body


async def post_raw(
    client: httpx.AsyncClient, body: bytes, content_type: str | None = None
) -> httpx.Response:
    headers = {"Content-Type": content_type or f"multipart/form-data; boundary={BOUNDARY}"}
    return await client.post("/api/v1/_test/upload", content=body, headers=headers)


def error(response: httpx.Response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


class CountingBody:
    """An async request body that records how many chunks the server pulled."""

    def __init__(self, chunk: bytes, count: int, prefix: bytes = b"") -> None:
        self.chunk, self.count, self.prefix = chunk, count, prefix
        self.pulled = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self.prefix:
            yield self.prefix
        for _ in range(self.count):
            self.pulled += 1
            yield self.chunk


# --- the happy path -------------------------------------------------------------------------


async def test_upload_is_received_hashed_and_detected(client: httpx.AsyncClient) -> None:
    response = await post_file(client, TEXT, filename="Meeting notes.md")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "filename": "Meeting notes.md",
        "declared_content_type": "text/plain",
        "size_bytes": len(TEXT),
        "sha256": hashlib.sha256(TEXT).hexdigest(),
        "mime_type": "text/markdown",
        "content_matches": True,
        "length_read": len(TEXT),
    }


async def test_large_upload_spills_to_disk_and_stays_intact(client: httpx.AsyncClient) -> None:
    data = (b"0123456789abcdef" * 65536)[:MAX_BYTES]  # exactly the limit, > the 1 MiB spool
    response = await post_file(client, data)
    assert response.status_code == 200
    assert response.json()["size_bytes"] == MAX_BYTES
    assert response.json()["content_matches"] is True


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("C:\\fakepath\\résumé.txt", "résumé.txt"),
        ("../../etc/passwd", "passwd"),
        ("invoice\u202efdp.txt", "invoicefdp.txt"),
    ],
)
async def test_filenames_are_sanitized(
    client: httpx.AsyncClient, filename: str, expected: str
) -> None:
    response = await post_file(client, TEXT, filename=filename)
    assert response.json()["filename"] == expected


# --- size limits ----------------------------------------------------------------------------


async def test_one_byte_over_the_limit_is_413(client: httpx.AsyncClient) -> None:
    response = await post_file(client, b"a" * (MAX_BYTES + 1))

    assert error(response) == (413, "FILE_TOO_LARGE")
    assert response.json()["error"]["details"] == {"max_bytes": MAX_BYTES}


async def test_declared_length_too_big_is_refused_before_reading(
    client: httpx.AsyncClient,
) -> None:
    body = CountingBody(b"x" * 65536, count=100)
    response = await client.post(
        "/api/v1/_test/upload",
        content=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
            "Content-Length": str(50 * MAX_BYTES),
        },
    )

    assert error(response) == (413, "FILE_TOO_LARGE")
    assert body.pulled == 0


async def test_oversized_stream_without_a_length_stops_early(client: httpx.AsyncClient) -> None:
    """Chunked upload of 64 MiB against a 1 MiB limit: refused after about 1 MiB."""
    head = (
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="big.txt"\r\n\r\n'
    )
    body = CountingBody(b"x" * 65536, count=1024, prefix=head.encode())
    response = await client.post(
        "/api/v1/_test/upload",
        content=body,
        headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
    )

    assert error(response) == (413, "FILE_TOO_LARGE")
    assert body.pulled <= MAX_BYTES // 65536 + 2  # ~17 of 1024 chunks


async def test_lying_content_length_cannot_sneak_past_the_limit(
    client: httpx.AsyncClient,
) -> None:
    head = f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n\r\n'
    body = CountingBody(b"x" * 65536, count=64, prefix=head.encode())
    response = await client.post(
        "/api/v1/_test/upload",
        content=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
            "Content-Length": "100",  # claims to be tiny
        },
    )

    assert response.status_code in {400, 413}  # never accepted
    assert body.pulled <= MAX_BYTES // 65536 + 2


@pytest.mark.parametrize("value", ["abc", "-1", "1e9"])
async def test_invalid_content_length_is_400(client: httpx.AsyncClient, value: str) -> None:
    response = await client.post(
        "/api/v1/_test/upload",
        content=multipart(("file", "a.txt", TEXT)),
        headers={
            "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
            "Content-Length": value,
        },
    )
    assert response.status_code == 400


# --- request shape --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "multipart/form-data", "multipart/mixed; boundary=x", "text/plain"],
)
async def test_wrong_content_type_is_400(client: httpx.AsyncClient, content_type: str) -> None:
    response = await post_raw(client, multipart(("file", "a.txt", TEXT)), content_type)
    assert error(response) == (400, "INVALID_UPLOAD")


async def test_missing_content_type_is_400(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/_test/upload", content=b"raw bytes")
    assert error(response) == (400, "INVALID_UPLOAD")


@pytest.mark.parametrize(
    ("parts", "message_part"),
    [
        ([("file", "a.txt", TEXT), ("file", "b.txt", TEXT)], "exactly one file"),
        ([("file", "a.txt", TEXT), ("note", None, b"hi")], "Unexpected form field"),
        ([("note", None, b"hi"), ("file", "a.txt", TEXT)], "Unexpected form field"),
        ([("document", "a.txt", TEXT)], "Unexpected form field"),
        ([("file", None, TEXT)], "must be a file"),
        ([], "No file was sent"),
    ],
)
async def test_only_a_single_file_field_is_accepted(
    client: httpx.AsyncClient, parts: list[tuple[str, str | None, bytes]], message_part: str
) -> None:
    response = await post_raw(client, multipart(*parts))
    assert error(response) == (400, "INVALID_UPLOAD")
    assert message_part in response.json()["error"]["message"]


@pytest.mark.parametrize(
    "body",
    [
        multipart(("file", "a.txt", TEXT), close=False),  # truncated: no closing boundary
        b"this is not multipart at all",
        f"--{BOUNDARY}\r\nno headers end".encode(),
    ],
)
async def test_malformed_or_truncated_bodies_are_400(
    client: httpx.AsyncClient, body: bytes
) -> None:
    assert error(await post_raw(client, body)) == (400, "INVALID_UPLOAD")


# --- type detection through the route -------------------------------------------------------


async def test_renamed_executable_is_415(client: httpx.AsyncClient) -> None:
    response = await post_file(client, b"MZ\x90\x00" + b"\x00" * 100, filename="invoice.pdf")
    assert error(response) == (415, "UNSUPPORTED_FILE_TYPE")


async def test_empty_file_is_422(client: httpx.AsyncClient) -> None:
    assert error(await post_file(client, b"")) == (422, "FILE_EMPTY")


# --- no temporary files left behind ---------------------------------------------------------


@pytest.fixture
def spools(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    created: list[Any] = []
    original = tempfile.SpooledTemporaryFile

    def tracking(*args: Any, **kwargs: Any) -> Any:
        spool = original(*args, **kwargs)
        created.append(spool)
        return spool

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", tracking)
    return created


@pytest.mark.parametrize(
    "body",
    [
        multipart(("file", "a.txt", b"a" * (MAX_BYTES + 1))),  # too large
        multipart(("file", "a.txt", TEXT), ("note", None, b"x")),  # invalid shape
        multipart(("file", "a.txt", TEXT), close=False),  # truncated
    ],
)
async def test_failed_uploads_close_their_spool(
    client: httpx.AsyncClient, spools: list[Any], body: bytes
) -> None:
    response = await post_raw(client, body)

    assert response.status_code in {400, 413}
    assert len(spools) == 1
    assert spools[0].closed


async def test_junk_after_the_closing_boundary_is_capped(client: httpx.AsyncClient) -> None:
    """Multipart allows an 'epilogue' after the last boundary, which parsers ignore. It
    is not file data, so only the overall body cap stops a client streaming it forever."""
    body = CountingBody(b"junk" * 16384, count=1024, prefix=multipart(("file", "a.txt", TEXT)))
    response = await client.post(
        "/api/v1/_test/upload",
        content=body,
        headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
    )

    assert error(response) == (413, "FILE_TOO_LARGE")
    assert body.pulled <= MAX_BYTES // 65536 + 2
