"""A fake web server and tiny rasters for the terrain snapshot command tests.

Nothing here touches the network. FakeWeb answers urllib requests from a dict
of URL to body and headers, the way the commands' opener argument expects, and
records every request so a test can check that a refusal happened before any
download.
"""

import email.message
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

import numpy as np
import numpy.typing as npt
import rasterio
from rasterio.transform import from_origin

from pipeline.terrain_snapshots.remote import PinnedObject

LAST_MODIFIED = "Fri, 13 Feb 2026 18:21:19 GMT"


class FakeResponse:
    """The part of http.client.HTTPResponse the commands use."""

    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self._body = body
        self._offset = 0
        self.headers = email.message.Message()
        for name, value in headers.items():
            self.headers[name] = value

    def read(self, size: int = -1) -> bytes:
        end = len(self._body) if size < 0 else min(len(self._body), self._offset + size)
        chunk = self._body[self._offset : end]
        self._offset = end
        return chunk

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


@dataclass
class FakeWeb:
    """Serves registered URLs; anything else is a 404."""

    objects: dict[str, tuple[bytes, dict[str, str]]] = field(default_factory=dict)
    requests: list[tuple[str, str]] = field(default_factory=list)

    def serve(
        self,
        url: str,
        body: bytes,
        *,
        etag: str = '"etag-1"',
        last_modified: str = LAST_MODIFIED,
        content_length: int | None = None,
    ) -> PinnedObject:
        """Register a body and return the pin that matches it."""
        length = len(body) if content_length is None else content_length
        headers = {"Content-Length": str(length), "ETag": etag, "Last-Modified": last_modified}
        self.objects[url] = (body, headers)
        return PinnedObject(url=url, bytes=len(body), etag=etag, last_modified=last_modified)

    def downloads(self) -> list[str]:
        return [url for method, url in self.requests if method == "GET"]

    def __call__(self, request: urllib.request.Request) -> FakeResponse:
        method = request.get_method()
        url = request.full_url
        self.requests.append((method, url))
        if url not in self.objects:
            raise urllib.error.HTTPError(url, 404, "Not Found", email.message.Message(), None)
        body, headers = self.objects[url]
        return FakeResponse(b"" if method == "HEAD" else body, headers)


def write_geotiff(
    path: Path,
    data: npt.NDArray[Any],
    *,
    epsg: int,
    west: float,
    north: float,
    res: float,
    nodata: float | None = None,
) -> bytes:
    """Write a north-up single or multi band GeoTIFF and return its bytes."""
    bands = data if data.ndim == 3 else data[np.newaxis]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=bands.shape[2],
        height=bands.shape[1],
        count=bands.shape[0],
        dtype=bands.dtype,
        crs=f"EPSG:{epsg}",
        transform=from_origin(west, north, res, res),
        nodata=nodata,
    ) as dst:
        dst.write(bands)
    return path.read_bytes()
