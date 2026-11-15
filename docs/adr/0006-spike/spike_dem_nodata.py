"""ADR 0006 spike, run 7: count nodata pixels in one remote 1 m DEM tile.

Usage: python -I spike_dem_nodata.py <url>   (run from an empty folder)

Evidence for ADR 0006 Open item 11, what the profile models do at a DEM pixel
with no value. The script streams the tile through GDAL's /vsicurl/ in windows
of 512 full-width rows, counts pixels equal to the nodata value or not finite,
and keeps nothing: no file is written and each window is dropped after it is
counted. It reads the live file the URL names, not a registered snapshot.

Behind a TLS-inspecting proxy, GDAL needs CURL_CA_BUNDLE pointing at the
proxy's certificate bundle.
"""

import sys
import time

import numpy as np
import rasterio
from rasterio.windows import Window

ROWS_PER_WINDOW = 512


def missing_mask(data, nodata):
    if nodata is None or np.isnan(nodata):
        return ~np.isfinite(data)
    return (data == nodata) | ~np.isfinite(data)


def main(url):
    start = time.time()
    with rasterio.open(f"/vsicurl/{url}") as src:
        print("crs", src.crs, "size", src.width, src.height, "res", src.res, "nodata", src.nodata,
              "dtype", src.dtypes[0], "bounds", src.bounds, "block", src.block_shapes[0])
        total = 0
        rows_with = []
        columns_with = np.zeros(src.width, dtype=bool)
        for row in range(0, src.height, ROWS_PER_WINDOW):
            height = min(ROWS_PER_WINDOW, src.height - row)
            missing = missing_mask(src.read(1, window=Window(0, row, src.width, height)), src.nodata)
            count = int(missing.sum())
            total += count
            if count:
                rows_with.append((row, count))
                columns_with |= missing.any(axis=0)
        print("nodata pixels", total, "of", src.width * src.height)
        if total:
            columns = np.flatnonzero(columns_with)
            print("first and last column with nodata", int(columns[0]), int(columns[-1]))
            print("windows with nodata (start row, count)", rows_with)
    print(f"elapsed {time.time() - start:.0f} s")


if __name__ == "__main__":
    main(sys.argv[1])
