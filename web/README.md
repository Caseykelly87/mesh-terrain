# web

The MapLibre site served from GitHub Pages, with PMTiles exports, and the scripts that draw the README's figures. The site is not built yet; it comes later in Phase 5.

Deliberately not here: any computation in the page. The page reads export files only. The figure scripts under `figures/` run once on a workstation, write a PNG that is committed under `docs/img/`, and are never part of the site.

## Coverage figure

`figures/coverage_png.py` turns one coverage file from an engine run (`coverage_<site_id>_<model>.tif`, predicted path loss in dB from one site on 10 m cells; `docs/INTERFACE.md`) into a paletted PNG. Run it from the repository root, with the config file the run used:

```
uv run --frozen python -I web/figures/coverage_png.py <run folder>/coverage_site-west_knife_edge.tif engine/fixtures/fixture-run.yaml docs/img/coverage-fixture.png
```

It refuses (exit 2, nothing written) unless the run folder has a manifest, the config is the one the manifest records, the coverage file is listed in the manifest with its sha256, and both point layers the config names (`tx_sites`, `rx_points`) are unchanged since the run and marked `synthetic`, and the coverage raster is north-up with square cells. That repeats the manual check made before a figure is published; it does not replace it. Coverage from a run whose sites include a real gateway is never published.

The figure is the committed fixture run (`engine/fixtures/fixture-run.yaml`): two synthetic sites, 500 synthetic receivers and synthetic 1 km terrain, so nothing in it is real. It shows site `site-west` under the `knife_edge` model, free-space loss plus the ITU-R P.526 Bullington diffraction loss over the terrain profile. `fspl` would draw distance rings only, `hata` reads no terrain profile, and the fixture's `p1812` numbers come from synthetic ITU maps and are not predictions.

Each 10 m cell is one class, enlarged to whole pixels; north is up and the area is about 1 km across. Colours are seven-class viridis, colour-blind safe, lightest where the predicted loss is least. Cells outside the receivers' hull have no value and are transparent.

Caption table, basic transmission loss with antenna gains excluded:

| Colour | Path loss, dB |
| --- | --- |
| `#fde725` | below 60 |
| `#90d743` | 60 to 70 |
| `#35b779` | 70 to 80 |
| `#21918c` | 80 to 90 |
| `#31688e` | 90 to 100 |
| `#443983` | 100 to 110 |
| `#440154` | 110 or more |
| none (transparent) | no value |

`tests/test_coverage_figure.py` tests the classes, the palette, this table and the refusals; the PNG itself is checked by eye.
