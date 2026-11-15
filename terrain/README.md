# terrain

Pure functions for terrain profiles, line of sight, Fresnel clearance, diffraction and the terrain-free baseline losses. Every coordinate is EPSG:26915 (UTM zone 15N, metres); every height is metres above NAVD88; frequencies are MHz.

| Module | Holds |
| --- | --- |
| `profile.py` | `PathProfile` (ADR 0006 decision 3) and `sample_profile`, which reads bounded rasterio windows from an already-open raster along a path and samples bare ground bilinearly at a fixed step: 1 m on the 1 m layers, 10 m on the metro DEM |
| `curvature.py` | `earth_bulge_m`, the k = 4/3 bulge that line of sight, Fresnel and knife edge add to the ground; profiles never carry it |
| `clearance.py` | `los_clear`, `obstruction_m`, `fresnel_radius_m`, `fresnel_clearance_pct` |
| `diffraction.py` | `knife_edge_db`: the Bullington method of ITU-R P.526-16 section 4.5.1, equations (49) to (57); `knife_edge_loss_db` is the single knife edge J(nu), equation (31) |
| `baselines.py` | `fspl_db`, `hata_db`, and `hata_variant` (urban at a mean building share of 0.20, ADR 0006 decision 7.1) |
| `clutter.py` | `clutter_height_m`, the per-pixel clutter rule of ADR 0006 item 6 (a) with the canopy fill of item 11 (i); `dsm_filled_mask`; `require_complete_clutter` |
| `cells.py` | `FineLayers` (the open 1 m DEM, DSM and canopy on one grid) and `cell_profile`, P.1812's 10 m profile from fixed 10 by 10 pixel cells (items 7 to 9) |
| `draws.py` | `sample_link`, the endpoint sampler: N draws per link-hour, seeded by `draw_seed`, profiles from `MetroProfileBuilder` or `FineProfileBuilder`, models through a function the caller passes in, medians, interquartile ranges, shares, refusal counts and building draw counts |
| `errors.py` | `OutOfExtentError` and `RefusedDrawError`, neither a `ValueError` (ADR 0006 item 11); `ZeroLengthPathError`, a `ValueError` subclass and the one error the feature asset catches, for a path whose endpoints are exactly 0 m apart |

A refused draw carries one of four reasons, the causes of ADR 0006 item 11 as amended by Casey on PR #30. Each engine run report counts refused draws under the names on the right; no column holds the breakdown.

| Reason code | Run report name | When |
| --- | --- | --- |
| `dem_gap` | missing map data | a ground profile point reads a DEM pixel without a value |
| `empty_cell` | empty 10 m cell | a 10 m cell of P.1812's profile holds a pixel without a value |
| `clutter_nan` | missing clutter | a clutter height is still missing after the fills of ADR 0006 decision 6 |
| `short_profile` | too-short profile | a profile has too few samples for a model: no interior sample for line of sight, Fresnel and knife edge, fewer than five for `path_loss_db` |

The knife-edge baseline reads well below ITM and P.1812 on paths with low antennas, by design. `knife_edge_db` counts the ground and the earth bulge as one Bullington edge but omits the ground-reflection and smooth-earth effect, the smooth-earth part of P.526-16 section 4.5.2, which is where most of the loss over open ground comes from when both antennas are near it. The physical reason: with 2 m antennas 10 km apart, the ray reflected off the ground arrives almost in antiphase with the direct ray and nearly cancels it, and P.526's smooth-earth part models that loss, while a single knife edge cannot (`docs/adr/0002-itm-spike/README.md` line 58). On the flat inland 10 km path at 915 MHz with 2 m antennas it gives 12.1 dB on top of 111.7 dB free space, 123.8 dB in all, against 149.5 dB from P.1812 and 151.8 dB from ITM (`docs/measurements-2026-11-08.md`, ADR 0002): 25.7 and 28.0 dB below them, about 26 to 28 dB. The write-up reports that gap as a finding, not an error.

A worked example: a 2 km path over flat ground at 150 m with a 25 m ridge 700 m from the transmitter and 2 m antennas at both ends. The ray between the antenna tops runs at 152 m; the ridge top, raised 0.05 m by the earth bulge, stands 23.05 m above it, so `los_clear` is false and `obstruction_m` is 23.05. At 915 MHz (wavelength 0.328 m) the P.526 parameter is nu = 23.05 x sqrt((2 / 0.328) x (1/700 + 1/1300)) = 2.67, the single edge loses J(2.67) = 21.43 dB (`knife_edge_loss_db`), and `knife_edge_db` applies P.526-16 equation (57) for the Bullington loss: 21.43 + (1 - exp(-21.43 / 6)) x (10 + 0.02 x 2) = 31.19 dB on top of free space.

## Clutter and the 10 m cells

Clutter is what stands on the ground: buildings and trees. Its height at each 1 m pixel comes from three layers: the lidar surface (DSM, roof and tree tops, flown in winter with the leaves off), the bare-earth DEM, and a canopy height map made from satellite images, much of them taken in summer. The rule, in order: DSM minus DEM; a negative result (lidar noise on bare ground) becomes 0; a pixel where the DSM has no value but the DEM has one (mostly water, which returns no lidar pulse) becomes 0; a canopy pixel with no value counts as 0; then the taller of that and the canopy. The canopy step matters because a bare winter tree lets most lidar pulses through to the ground, so the lidar sees a 15 m tree as 2 m.

P.1812 then gets its own profile every 10 m, because the Recommendation warns that 1 m surface data describes single obstacles and overstates the loss. Ground and clutter are each averaged over fixed 10 m squares (cells) on the 1 m grid, and the profile reads the cell averages every 10 m, blending the four nearest cells. The two ends take the exact 1 m ground under each antenna, so every model puts an antenna at the same height, and carry no clutter, as P.1812 adds none at the terminals. A cell holding any pixel without a value has none, and the draw that reads it is refused.

A worked example (ADR 0006 item 6): a cell half covered by a roof 8 m above the ground and half by a tree that the leaf-off lidar sees at 2 m and the canopy map at 15 m. Per pixel, the roof half is 8 m and the tree half max(2, 15) = 15 m, so the cell's clutter is (8 + 15) / 2 = 11.5 m. Averaging the layers first would give max(5, 7.5) = 7.5 m and lose most of the tree.

## Endpoint draws

`sample_link` places both endpoints at random inside their position boxes 16 times per link-hour (a draw each time), seeded from the run seed and the link id so a rerun gives the same draws, builds the profiles, runs the models through the function the caller passes in, and keeps the median and interquartile range of every number and the share of draws for every yes or no result. A refused draw is dropped for every model and counted under its reason; with every draw refused, every median is null. With one draw the endpoints are the box centres.

Run: the functions are imported by the engine and the feature asset; `uv run pytest tests/test_terrain_*.py` exercises them on synthetic DEMs written per test.

Deliberately not here: raster downloads, database access, Dagster objects, the building-pixel raster (rasterised from the OpenStreetMap snapshot elsewhere and passed in as an array), the Okumura-Hata variant choice per link, and the ITM and P.1812 wrappers (`itm.py`, `p1812.py`). `sample_profile` and `cell_profile` are the only functions that touch a file: they read windows of rasters the caller has opened, and open nothing themselves.
