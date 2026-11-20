# terrain

Pure functions for terrain profiles, line of sight, Fresnel clearance, diffraction, the terrain-free baseline losses, and the ITM and P.1812 wrappers. Every coordinate is EPSG:26915 (UTM zone 15N, metres); every height is metres above NAVD88; frequencies are MHz.

| Module | Holds |
| --- | --- |
| `profile.py` | `PathProfile` (ADR 0006 decision 3) and `sample_profile`, which reads bounded rasterio windows from an already-open raster along a path and samples bare ground bilinearly at a fixed step: 1 m on the 1 m layers, 10 m on the metro DEM |
| `curvature.py` | `earth_bulge_m`, the k = 4/3 bulge that line of sight, Fresnel and knife edge add to the ground; profiles never carry it |
| `clearance.py` | `los_clear`, `obstruction_m`, `fresnel_radius_m`, `fresnel_clearance_pct` |
| `diffraction.py` | `knife_edge_db`: the Bullington method of ITU-R P.526-16 section 4.5.1, equations (49) to (57); `knife_edge_loss_db` is the single knife edge J(nu), equation (31) |
| `baselines.py` | `fspl_db`, `hata_db`, and `hata_variant` (urban at a mean building share of 0.20, ADR 0006 decision 7.1) |
| `clutter.py` | `clutter_height_m`, the per-pixel clutter rule of ADR 0006 item 6 (a) with the canopy fill of item 11 (i); `dsm_filled_mask`; `require_complete_clutter` |
| `cells.py` | `FineLayers` (the open 1 m DEM, DSM and canopy on one grid) and `cell_profile`, P.1812's 10 m profile from fixed 10 by 10 pixel cells (items 7 to 9) |
| `boxes.py` | `EndpointBox`, a square on the EPSG:26915 grid, and `EdgeBox`, the `lnk_02` box by its edges in 1e-7 degrees; each places drawn points, and `GEOGRAPHIC_TO_UTM` is the one conversion (ADR 0008) |
| `draws.py` | `sample_link`, the endpoint sampler: N draws per link-hour, seeded by `draw_seed`, profiles from `MetroProfileBuilder` or `FineProfileBuilder`, models through a function the caller passes in, medians, interquartile ranges, shares, refusal counts and building draw counts |
| `errors.py` | `OutOfExtentError` and `RefusedDrawError`, neither a `ValueError` (ADR 0006 item 11); `ZeroLengthPathError`, a `ValueError` subclass and the one error the feature asset catches, for a path whose endpoints are exactly 0 m apart |
| `result.py` | `PathLossResult`, what every model returns for one draw: the loss in dB plus diagnostics only some models fill, Lbs for P.1812 and the warning flags for ITM (ADR 0006 item 10 (a)) |
| `itm.py` | `itm_loss`: the Irregular Terrain Model (Longley-Rice), point-to-point, through NTIA's C++ library and `ctypes` (ADR 0002); the library exists only in the Dagster image, at `ITM_LIBRARY_PATH` |
| `p1812.py` | `p1812_loss` and `load_maps`: ITU-R P.1812-8 through Py1812 (ADR 0006), imported against an empty in-memory placeholder for the ITU maps, with DN and N0 from a verified map snapshot passed on every call; import it lazily, on the first P.1812 call, so loading the Dagster definitions never runs Py1812's import |
| `antenna.py` | `AntennaProfile`, one named profile record of `docs/INTERFACE.md`: height, gain, transmit power, polarisation, description |
| `path_loss.py` | `path_loss_db`, the one function over the five models (ADR 0006 decision 3), and `DrawModels`, the function `sample_link` calls per draw to run them; it imports `p1812.py` on the first P.1812 call only |

A refused draw carries one of four reasons, the causes of ADR 0006 item 11 as amended by Casey on PR #30. Each engine run report counts refused draws under the names on the right; no column holds the breakdown.

| Reason code | Run report name | When |
| --- | --- | --- |
| `dem_gap` | missing map data | a ground profile point reads a DEM pixel without a value |
| `empty_cell` | empty 10 m cell | a 10 m cell of P.1812's profile holds a pixel without a value |
| `clutter_nan` | missing clutter | a clutter height is still missing after the fills of ADR 0006 decision 6; `require_complete_clutter` raises it for a profile built any other way. On the 1 m layers the count is 0: `cell_profile` refuses a cell with a missing clutter pixel as `empty_cell` first |
| `short_profile` | too-short profile | a profile has too few samples for a model: no interior sample for line of sight, Fresnel and knife edge, fewer than five for `path_loss_db` |

The knife-edge baseline reads well below ITM and P.1812 on paths with low antennas, by design. `knife_edge_db` counts the ground and the earth bulge as one Bullington edge but omits the ground-reflection and smooth-earth effect, the smooth-earth part of P.526-16 section 4.5.2, which is where most of the loss over open ground comes from when both antennas are near it. The physical reason: with 2 m antennas 10 km apart, the ray reflected off the ground arrives almost in antiphase with the direct ray and nearly cancels it, and P.526's smooth-earth part models that loss, while a single knife edge cannot (`docs/adr/0002-itm-spike/README.md` line 58). On the flat inland 10 km path at 915 MHz with 2 m antennas it gives 12.1 dB on top of 111.7 dB free space, 123.8 dB in all, against 149.5 dB from P.1812 and 151.8 dB from ITM (`docs/measurements-2026-11-08.md`, ADR 0002): 25.7 and 28.0 dB below them, about 26 to 28 dB. The write-up reports that gap as a finding, not an error.

A worked example: a 2 km path over flat ground at 150 m with a 25 m ridge 700 m from the transmitter and 2 m antennas at both ends. The ray between the antenna tops runs at 152 m; the ridge top, raised 0.05 m by the earth bulge, stands 23.05 m above it, so `los_clear` is false and `obstruction_m` is 23.05. At 915 MHz (wavelength 0.328 m) the P.526 parameter is nu = 23.05 x sqrt((2 / 0.328) x (1/700 + 1/1300)) = 2.67, the single edge loses J(2.67) = 21.43 dB (`knife_edge_loss_db`), and `knife_edge_db` applies P.526-16 equation (57) for the Bullington loss: 21.43 + (1 - exp(-21.43 / 6)) x (10 + 0.02 x 2) = 31.19 dB on top of free space.

## One function for every model

`path_loss_db(model, profile, frequency_mhz, tx, rx, ...)` returns a `PathLossResult` for `fspl`, `knife_edge`, `hata`, `itm` and `p1812`: the basic transmission loss, which leaves out transmit power and both antenna gains. The knife-edge model is free space plus the Bullington loss, so its number compares with the others. On the flat inland 10 km path at 915 MHz with `mesh_node` (2 m) at both ends it gives 111.68 dB for `fspl`, 123.81 dB for `knife_edge`, 151.76 dB for `itm` and 149.48 dB for `p1812` with the St. Louis DN and N0, whose troposcatter loss Lbs is 160.17 dB, so that result is not at the cap. `hata` needs its variant from the caller, `itm` the loaded library, and `p1812` the map grids and a profile with clutter. Every model refuses a profile with fewer than five samples as `short_profile` and a missing ground height as `dem_gap`, and raises `ZeroLengthPathError` when the endpoints coincide.

`DrawModels` runs a run's models on one draw for `sample_link`. It reports each loss as `<model>_db` and P.1812's cap as the flag `p1812_at_cap`, so a link's summary gives `median("itm_db")` for the `itm_db_median` column and `share("p1812_at_cap")` for `p1812_at_cap_share`. On the metro DEM a draw has no P.1812 profile, and `p1812` is left out, so its columns are null there.

## Clutter and the 10 m cells

Clutter is what stands on the ground: buildings and trees. Its height at each 1 m pixel comes from three layers: the lidar surface (DSM, roof and tree tops, flown in winter with the leaves off), the bare-earth DEM, and a canopy height map made from satellite images, much of them taken in summer. The rule, in order: DSM minus DEM; a negative result (lidar noise on bare ground) becomes 0; a pixel where the DSM has no value but the DEM has one (mostly water, which returns no lidar pulse) becomes 0; a canopy pixel with no value counts as 0; then the taller of that and the canopy. The canopy step matters because a bare winter tree lets most lidar pulses through to the ground, so the lidar sees a 15 m tree as 2 m.

P.1812 then gets its own profile every 10 m, because the Recommendation warns that 1 m surface data describes single obstacles and overstates the loss. Ground and clutter are each averaged over fixed 10 m squares (cells) on the 1 m grid, with edges on eastings and northings that are multiples of 10 m, so a link averages the same squares from whichever raster it is read, and the profile reads the cell averages every 10 m, blending the four nearest cells. The two ends take the 1 m DEM at each endpoint, so every model puts an antenna at the same height, and carry no clutter, as P.1812 adds none at the terminals; the end points themselves read no cell. A cell holding any pixel without a value has none, and the draw whose profile reads it is refused, including the endpoint's own cell when the first or last interior sample's bilinear read reaches it.

A worked example (ADR 0006 item 6): a cell half covered by a roof 8 m above the ground and half by a tree that the leaf-off lidar sees at 2 m and the canopy map at 15 m. Per pixel, the roof half is 8 m and the tree half max(2, 15) = 15 m, so the cell's clutter is (8 + 15) / 2 = 11.5 m. Averaging the layers first would give max(5, 7.5) = 7.5 m and lose most of the tree.

## Endpoint draws

`sample_link` places both endpoints at random inside their position boxes 16 times per link-hour (a draw each time), seeded from the run seed and the link id so a rerun gives the same draws, builds the profiles, runs the models through the function the caller passes in, and keeps the median and interquartile range of every number and the share of draws for every yes or no result. A refused draw is dropped for every model and counted under its reason; with every draw refused, every median is null. With one draw the endpoints are the box centres.

A box is a square on the EPSG:26915 grid or the `lnk_02` box itself, a rectangle in latitude and longitude (ADR 0008). A 16-bit cell at tile x72y429 is 727.5 m north to south and 570.2 m east to west, and on the UTM grid it is turned by about 1.6 degrees, true north lying west of grid north. The sampler draws a point evenly between the cell's edges in latitude and longitude and converts it with pyproj, so every draw lands inside the cell and the turn comes from the conversion; that one interpolation between a box's own edges is the exception to computing in EPSG:26915 that Casey granted on PR #73. A square draws the same numbers it always has.

The draws never use a half-width below the GPS error floor, `floor_m` (ADR 0009; the engine passes the run config's `position_floor_m`, 5 m in prepared runs). 5 m is a minimum covering about 95 percent of open-sky fixes of a u-blox M8-class receiver, whose data sheet gives 2.5 m CEP. A square smaller than the floor draws as a square of the floor; an edge box is widened about its centre on each axis below the floor, in latitude and longitude with `pos_01`'s radii, so a 32-bit cell draws over 10 m by 10 m. Exact points stay exact (terrain-rf-eng's proposal on PR #89, awaiting Casey), and a 15-bit or 16-bit cell draws as before.

Run: the functions are imported by the engine and the feature asset; `uv run pytest tests/test_terrain_*.py tests/test_path_loss.py tests/test_itm.py tests/test_p1812.py tests/test_p1812_cap.py` exercises them on synthetic DEMs written per test. The ITM tests that call libitm.so skip outside the Dagster image.

Deliberately not here: raster downloads, database access, Dagster objects, the building-pixel raster (rasterised from the OpenStreetMap snapshot elsewhere and passed in as an array) and the Okumura-Hata variant choice per link. Four functions touch a file: `sample_profile` and `cell_profile` read windows of rasters the caller has opened, and open nothing themselves; `load_itm_library` loads the ITM shared library from the path its caller passes (ADR 0002); and `load_maps` reads the two ITU map files from a snapshot folder once per run, after checking their sha256 against the snapshot manifest (ADR 0006 decision 2). Casey accepted both reads on PR #31.

## Map snapshots for P.1812

- Real runs name `itu-r-p1812-8-202509`, written by `make p1812-maps` under `SNAPSHOTS_DIR` and never committed.
- Tests and the CI engine fixture name `tests/fixtures/snapshots/p1812-maps-synthetic`: the real grid shape filled with made-up constants (DN 40, N0 325). P.1812 numbers from it are not predictions.

## Acknowledgements

- The ITM code is from the National Telecommunications and Information Administration (NTIA), Institute for Telecommunication Sciences, published as public domain software (github.com/NTIA/itm). The two changes made to build it on Linux are recorded with their dates in `docker/dagster/Dockerfile` and `docs/adr/0002-itm-spike/README.md`.
- Py1812 is by Ivica Stevanovic, Federal Office of Communications (OFCOM), Switzerland (github.com/eeveetza/Py1812), used unmodified at commit 6c9061dd under its MIT-style licence.
- ITU-R Recommendation P.1812-8 (09/2025) and its digital maps DN50 and N050 are from the International Telecommunication Union. The maps "may not be reproduced, by any means whatsoever, without written permission of ITU", so no copy of them is in this repository or any image.
