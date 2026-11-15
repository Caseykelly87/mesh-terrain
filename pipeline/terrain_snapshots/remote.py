"""Pinned HTTP downloads for the terrain snapshot commands.

A pin is what we measured about a remote file before writing the command: its
size, its ETag, its Last-Modified date, and its sha256 when known. Several of
these sources (the USGS and Meta S3 buckets) have no object versioning, so a
file can be replaced in place under the same URL. Every command therefore asks
for the headers first and refuses before downloading when any pinned field has
changed, then hashes what it received and refuses again if the size or the
pinned sha256 differ. The record each download returns goes into the snapshot
manifest beside the file's own sha256.

The opener argument is urllib.request.urlopen in production and a fake in the
tests; nothing here reads environment variables. Behind a proxy that re-signs
TLS, Python's ssl module reads SSL_CERT_FILE itself.
"""

import hashlib
import time
import urllib.request
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, fields, replace
from email.message import Message
from pathlib import Path
from typing import Protocol

import structlog

from pipeline.common.snapshot import SnapshotError

log = structlog.get_logger(component="pipeline.terrain_snapshots.remote")

TIMEOUT_S = 120
_CHUNK_BYTES = 1 << 20


class Response(Protocol):
    """The part of http.client.HTTPResponse the downloads use."""

    headers: Message

    def read(self, size: int = -1) -> bytes: ...


Opener = Callable[[urllib.request.Request], AbstractContextManager[Response]]


def urlopen_opener(request: urllib.request.Request) -> AbstractContextManager[Response]:
    """Open a request with urllib and the module timeout."""
    response: AbstractContextManager[Response] = urllib.request.urlopen(request, timeout=TIMEOUT_S)
    return response


class PinMismatchError(SnapshotError):
    """A remote file differs from what the command pinned."""


class DownloadError(SnapshotError):
    """A download did not complete as a whole file."""


@dataclass(frozen=True)
class PinnedObject:
    """A remote file and the fields that must still hold; None leaves a field open.

    Attributes:
        url: The file's URL.
        bytes: Size in bytes.
        etag: The ETag header as served, quotes included.
        last_modified: The Last-Modified header as served.
        sha256: Hex sha256 of the body.
    """

    url: str
    bytes: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    sha256: str | None = None


def head(url: str, opener: Opener = urlopen_opener) -> PinnedObject:
    """The size, ETag and Last-Modified a server reports for a URL, from a HEAD request."""
    with opener(urllib.request.Request(url, method="HEAD")) as response:
        headers = response.headers
    length = headers.get("Content-Length")
    return PinnedObject(
        url=url,
        bytes=int(length) if length is not None else None,
        etag=headers.get("ETag"),
        last_modified=headers.get("Last-Modified"),
    )


def check_pin(pin: PinnedObject, observed: PinnedObject) -> None:
    """Compare every field the pin sets with what was observed.

    Raises:
        PinMismatchError: naming each field that differs, pinned and observed.
    """
    differing = [
        f"{field.name} pinned {getattr(pin, field.name)!r}, found {getattr(observed, field.name)!r}"
        for field in fields(pin)
        if field.name != "url" and getattr(pin, field.name) is not None
        if getattr(pin, field.name) != getattr(observed, field.name)
    ]
    if differing:
        raise PinMismatchError(f"{pin.url} changed: {'; '.join(differing)}")


def read_small(url: str, opener: Opener = urlopen_opener, limit: int = 1 << 16) -> bytes:
    """The body of a small file such as a checksum file, read into memory.

    Raises:
        DownloadError: if the body is longer than limit bytes.
    """
    with opener(urllib.request.Request(url)) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise DownloadError(f"{url} is longer than {limit} bytes")
    return body


@dataclass(frozen=True)
class Downloaded:
    """What a download wrote: the byte count and two digests of the body."""

    bytes: int
    sha256: str
    md5: str


def download(url: str, dest: Path, opener: Opener = urlopen_opener) -> Downloaded:
    """Stream a URL into a new file, hashing as it goes.

    Raises:
        DownloadError: if dest exists, or the body is shorter or longer than
            the Content-Length the server sent.
    """
    if dest.exists():
        raise DownloadError(f"{dest} exists; a download never overwrites a file")
    sha256, md5, total = hashlib.sha256(), hashlib.md5(usedforsecurity=False), 0
    started = time.monotonic()
    log.info("download_begin", url=url, path=str(dest))
    with opener(urllib.request.Request(url)) as response, dest.open("xb") as handle:
        expected = response.headers.get("Content-Length")
        while chunk := response.read(_CHUNK_BYTES):
            handle.write(chunk)
            sha256.update(chunk)
            md5.update(chunk)
            total += len(chunk)
    if expected is not None and int(expected) != total:
        raise DownloadError(f"{url}: received {total} bytes, Content-Length says {expected}")
    duration = round(time.monotonic() - started, 3)
    log.info("download_end", url=url, path=str(dest), bytes=total, duration_s=duration)
    return Downloaded(bytes=total, sha256=sha256.hexdigest(), md5=md5.hexdigest())


def fetch_pinned(
    pin: PinnedObject, dest: Path, opener: Opener = urlopen_opener
) -> dict[str, object]:
    """Check a pinned file's headers, download it, and check its size and sha256.

    Returns:
        The manifest record: url, path (the file name), bytes, etag,
        last_modified and sha256 of what was written.

    Raises:
        PinMismatchError: before the download when a header differs from the
            pin, after it when the size or the pinned sha256 differs.
        DownloadError: as download does.
    """
    observed = head(pin.url, opener)
    check_pin(replace(pin, sha256=None), observed)
    got = download(pin.url, dest, opener)
    check_pin(
        pin,
        PinnedObject(
            url=pin.url,
            bytes=got.bytes,
            sha256=got.sha256,
            etag=observed.etag,
            last_modified=observed.last_modified,
        ),
    )
    return {
        "url": pin.url,
        "path": dest.name,
        "bytes": got.bytes,
        "etag": observed.etag,
        "last_modified": observed.last_modified,
        "sha256": got.sha256,
    }
