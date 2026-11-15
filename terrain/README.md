# terrain

Pure functions for terrain profiles, line of sight, Fresnel clearance, diffraction and the terrain-free baseline losses. Every coordinate is EPSG:26915 (UTM zone 15N, metres); every height is metres above NAVD88; frequencies are MHz.

| Module | Holds |
| --- | --- |
| `profile.py` | `PathProfile` (ADR 0006 decision 3) and `sample_profile`, which reads bounded rasterio windows from an already-open raster along a path and samples bare ground bilinearly at a fixed step: 1 m on the 1 m layers, 10 m on the metro DEM |
| `curvature.py` | `earth_bulge_m`, the k = 4/3 bulge that line of sight, Fresnel and knife edge add to the ground; profiles never carry it |
| `clearance.py` | `los_clear`, `obstruction_m`, `fresnel_radius_m`, `fresnel_clearance_pct` |
| `diffraction.py` | `knife_edge_db`: the Bullington method of ITU-R P.526-16 section 4.5.1, equations (49) to (57); `knife_edge_loss_db` is the single knife edge J(nu), equation (31) |
| `baselines.py` | `fspl_db`, `hata_db`, and `hata_variant` (urban at a mean building share of 0.20, ADR 0006 decision 7.1) |
| `errors.py` | `OutOfExtentError` and `RefusedDrawError`, neither a `ValueError` (ADR 0006 item 11); `ZeroLengthPathError`, a `ValueError` subclass and the one error the feature asset catches, for a path whose endpoints coincide |

A worked example: a 2 km path over flat ground at 150 m with a 25 m ridge 700 m from the transmitter and 2 m antennas at both ends. The ray between the antenna tops runs at 152 m; the ridge top, raised 0.05 m by the earth bulge, stands 23.05 m above it, so `los_clear` is false and `obstruction_m` is 23.05. At 915 MHz (wavelength 0.328 m) the P.526 parameter is nu = 23.05 x sqrt((2 / 0.328) x (1/700 + 1/1300)) = 2.67, the single edge loses J(2.67) = 21.43 dB (`knife_edge_loss_db`), and `knife_edge_db` applies P.526-16 equation (57) for the Bullington loss: 21.43 + (1 - exp(-21.43 / 6)) x (10 + 0.02 x 2) = 31.19 dB on top of free space.

Run: the functions are imported by the engine and the feature asset; `uv run pytest tests/test_terrain_*.py` exercises them on synthetic DEMs written per test.

Deliberately not here: raster downloads, database access, Dagster objects, the clutter rule and the 10 m cells for P.1812, endpoint draws (unit U3b), and the ITM and P.1812 wrappers (`itm.py`, `p1812.py`). `sample_profile` is the only function that touches a file: it reads windows of a raster the caller has opened, and opens nothing itself.
