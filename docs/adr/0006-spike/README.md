# P.1812 spike for ADR 0006

Evidence for `docs/adr/0006-p1812.md`. Run on 2026-11-14 on Casey's Windows 11 machine, in a scratch uv environment outside the repository's `.venv`: uv 0.13.4, CPython 3.12.2, numpy 2.5.4. Nothing here is production code, and no ITU file is in this folder or anywhere in git.

## What was fetched

Only the two public downloads the brief allowed, each into its own new, empty folder in the session scratch directory:

- Py1812 from github.com/eeveetza/Py1812 at commit `6c9061dd022e93c84169059a016643926ce89964`, the head of `main` on 2026-11-14 (`git ls-remote`; committed 2026-08-28, "Fix for Issue #11 (#14)"). No tags, no releases, no open issues. The only other branch, `bugfix/timestamp`, last changed on 2025-08-19.
- The ITU zip `https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.1812-8-202509-I!!ZIP-E.zip`, 635 849 bytes.

## Checksums

| File | sha256 |
| --- | --- |
| `R-REC-P.1812-8-202509-I.zip` | `d3a51f296e24f5d4f99daddcac8a38cb310fcb2081efef5d120f8f27bd8c5686` |
| `DN50.TXT` (from the zip, 262 691 bytes) | `4847bf9542b775850daab6f0fb1bdc933a4d9a28aff65549bcfc45e141cd230d` |
| `N050.TXT` (from the zip, 262 691 bytes) | `d27590ce5c7cf7ff7b4457ce31b9bf3591f0b97e367732195de0f1a98bd35f2a` |
| `P1812.npz` built from the two files with numpy 2.5.4, 467 078 bytes | `748878ea23c1b45b51a2de0454f44fac02076c7eface668c15f4d3e0809a13f3` |
| Py1812 archive `.../archive/6c9061dd022e93c84169059a016643926ce89964.tar.gz` | `03d7c5bd2bdb831d009266b4a17a2c721f63db4f817f60af47bb82fdc4268469` |

The zip also holds `ReadMe.doc`, `LAT.TXT` and `LON.TXT`, which P.1812 does not read. Building the npz twice gave the same bytes. Under the recommended option 2.2 no npz is built from the maps at all; the snapshot keeps the zip and the two text files.

## Commands

`$S` is the scratch directory. Norton re-signs HTTPS on this machine, hence `UV_SYSTEM_CERTS` and `--cacert`.

```
cd $S/p1812-env
UV_SYSTEM_CERTS=true uv venv --python 3.12 .venv
UV_SYSTEM_CERTS=true uv pip install --python .venv \
  "py1812 @ git+https://github.com/eeveetza/Py1812@6c9061dd022e93c84169059a016643926ce89964"

cd $S/itu-zip
curl -fsS --cacert C:/Users/Casey/.certs/ca-bundle.pem -o R-REC-P.1812-8-202509-I.zip \
  "https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.1812-8-202509-I!!ZIP-E.zip"
mkdir $S/itu-extracted && cd $S/itu-extracted
unzip -q $S/itu-zip/R-REC-P.1812-8-202509-I.zip N050.TXT DN50.TXT

cd $S/scripts
$S/p1812-env/.venv/Scripts/python.exe -I spike_p1812.py $S/itu-extracted $S/p1812-data
$S/p1812-env/.venv/Scripts/python.exe -I spike_placeholder.py $S/itu-extracted $S/placeholder-run
```

Run 3 has its own command below.

## Run 1: maps in a data folder (`spike_p1812.py`)

`spike_p1812.py` (this folder) builds `P1812.npz` into `$S/p1812-data` with the same `np.loadtxt` and `np.savez` calls as upstream's `initiate_digital_maps.py`, then imports `Py1812.P1812` with `importlib.resources.files` patched for the length of the import, and repeats the flat-path call of `docs/measurements-2026-11-08.md`: 10 km, 150 m above sea level, 0.915 GHz, p 50, 2 m antennas, vertical, zone 4, from 38.63 N 90.25 W toward the east.

Output of the second run, after the review fixes to the script's docstring and imports:

```
package dir: <scratch>\p1812-env\.venv\Lib\site-packages\Py1812
npz in package before: False
plain import without maps: FileNotFoundError: <scratch>\p1812-env\.venv\Lib\site-packages\Py1812\P1812.npz
built <scratch>\p1812-data\P1812.npz (467078 bytes) sha256 748878ea23c1b45b51a2de0454f44fac02076c7eface668c15f4d3e0809a13f3
  DN50.TXT sha256 4847bf9542b775850daab6f0fb1bdc933a4d9a28aff65549bcfc45e141cd230d
  N050.TXT sha256 d27590ce5c7cf7ff7b4457ce31b9bf3591f0b97e367732195de0f1a98bd35f2a
rebuilt npz identical: True
maps loaded: ['DN50', 'N050'] shapes [(121, 241), (121, 241)]
flat 10 km, 10 m step, maps:      Lb 149.48 dB  Ep 49.11 dBuV/m
path centre DN 39.126 N-units/km, N0 324.511 N-units, k50 1.3319
same call with DN, N0 passed:     Lb 149.48 dB
timing 10.0 m step (1001 points): Lb 149.48 dB, 2.9 ms a call
timing  1.0 m step (10001 points): Lb 149.48 dB, 18.3 ms a call
npz in package after: False
package files: ['P1812.py', '__init__.py', '__pycache__', 'initiate_digital_maps.py', 'maps']
```

What it shows:

- Without the maps, `import Py1812.P1812` fails at import with the site-packages path in the message. Our wrapper has to check first and raise its own error naming the install step.
- With the redirect, the maps load from a folder outside site-packages and the package folder stays as installed.
- 149.48 dB and 49.11 dBuV/m match the 2026-11-08 measurement. Passing the path-centre DN and N0 explicitly gives the same loss, so those two numbers are the only thing the maps contribute to a call.
- DN 39.126 gives k50 = 157 / (157 - DN) = 1.332, P.1812's median effective earth radius factor at St. Louis.
- The first run printed the same lines except the timings, 2.1 ms and 15.7 ms, so a call takes 2 to 3 ms at 1 001 points and 16 to 18 ms at 10 001 on this machine.

## Run 2: placeholder file, DN and N0 passed (`spike_placeholder.py`)

The first form of the option 2.2 import, replaced by run 3 after the second review. The script writes a placeholder `P1812.npz` holding two 1 by 1 grids of NaN, no ITU data. It refuses to go on if `Py1812.P1812` is already imported or the installed package holds an npz, then imports Py1812 with the map load pointed at the placeholder. It reads the two ITU text files with `np.loadtxt`, takes the path centre from Py1812's `great_circle_path` and interpolates DN and N0 with Py1812's `interp2`. Finally it makes the flat-path call with them, and once more without them.

```
placeholder npz: 518 bytes
import with placeholder: ok, DN50 shape (1, 1)
path centre 38.63001 N -90.19244 E: DN 39.126, N0 324.511
DN and N0 passed: Lb 149.48 dB  Ep 49.11 dBuV/m
DN and N0 omitted: IndexError: index 34 is out of bounds for axis 0 with size 1
```

The placeholder satisfies the import. The real maps enter only through the two numbers passed on each call, and a call that forgets them fails instead of returning a number built on the placeholder. This run did not check the package folder after the import; run 3 does.

## Run 3: in-memory placeholder and NaN in R (`spike_inmemory.py`)

This is the import option 2.2 now recommends, after the second review: the same as run 2, but the placeholder is built in memory and never written. The patched `files("Py1812")` returns an object whose `joinpath("P1812.npz")` returns an `io.BytesIO` holding `np.savez(DN50=nan, N050=nan)`. The script then checks what Py1812 does with a NaN in R. It ran with `-W error::RuntimeWarning`, so a numpy warning would have stopped it.

```
$S/p1812-env/.venv/Scripts/python.exe -I -W error::RuntimeWarning spike_inmemory.py $S/itu-extracted
```

```
import with in-memory placeholder: ok, DN50 shape (1, 1)
P1812.files after import is the replacement: True
npz in package after import: False
flat, R 0, DN and N0 passed: Lb 149.48 dB
flat, R 0, DN and N0 omitted: IndexError: index 34 is out of bounds for axis 0 with size 1
flat, R 10: Lb 160.170552 dB; NaN at point 300: 160.170552 dB; 0 at point 300: 160.170552 dB
flat, R 0: Lb 149.479180 dB; NaN at point 500: 149.479175 dB; 0 at point 500: 149.479180 dB
flat, one 30 m clutter point at 500: Lb 159.742181 dB; NaN at point 500: 149.479175 dB; 0 at point 500: 149.479180 dB
hilly, R 10: Lb 190.228103 dB; NaN at point 400: 190.228103 dB; 0 at point 400: 190.228103 dB
flat, R 10: Lb 160.170552 dB; NaN at point 1: 137.361142 dB; 0 at point 1: 160.170552 dB
flat, R 10: Lb 160.170552 dB; NaN at point 999: 160.170552 dB; 0 at point 999: 160.170552 dB
hilly, R 10: Lb 190.228103 dB; NaN at point 1: 137.361202 dB; 0 at point 1: 190.224333 dB
hilly, R 0: Lb 176.549174 dB; NaN at point 1: 137.361202 dB; 0 at point 1: 176.549174 dB
hilly, R 10: Lb 190.228103 dB; NaN at point 999: 190.224333 dB; 0 at point 999: 190.224333 dB
```

What it shows:

- The in-memory placeholder gives the same import, the same 149.48 dB with DN and N0, and the same `IndexError` without them as run 2's file. The installed package holds no npz after the import. `P1812.files` keeps the replacement after the import, which is harmless because line 19 is its only use.
- A NaN in R never makes Lb NaN, and no warning is raised. The NaN enters `g = h + R` (`P1812.py` line 330). At most points the comparisons after it skip that point, as though it had no clutter: on the flat path with R = 10 m a NaN at index 300 or 999 leaves 160.17 dB. Where the skipped point is the one obstacle that sets the loss, the obstacle vanishes: the single 30 m clutter point gives 159.74 dB, and the same point marked NaN gives 149.48 dB, the bare-ground figure. At index 1, the first interior point next to the transmitter, a NaN does worse. `dl_bull` (lines 1819 to 1846) takes its largest slope with Python's built-in `max`, which returns NaN when the first element is NaN; every test after that is false, and the knife-edge loss of the whole path is 0. The flat path then gives 137.36 dB, below the bare-ground 149.48 dB, and the hilly path (hills rising 0 to 40 m above 150 m: 190.23 dB with R = 10 m, 176.55 dB bare) also gives 137.36 dB, 52.87 dB too low. So a NaN is skipped, except at the first interior point, where it removes the path's diffraction loss, and the wrapper has to fill or refuse every NaN in R before it calls `bt_loss`.

## Run 4: clutter at the terminals and the troposcatter cap (`spike_terminal.py`, `spike_floor.py`)

For Casey's addition C, after his decision of 2026-11-14. Same in-memory import, flat ground at 150 m unless a case gives hills, St. Louis DN 39.126 and N0 324.511, 915 MHz, p 50, 2 m antennas unless stated. Both ran with `-W error::RuntimeWarning`.

The text of P.1812-8 was read from the ITU's public PDF, `https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.1812-8-202509-I!!PDF-E.pdf` (1 137 557 bytes, 36 pages, sha256 `0c106ef6d2dfe1b287fd88756350a26719d922be8b88520da3203c65b80d4a7e`), fetched into its own folder in the scratch directory and not committed.

```
$S/p1812-env/.venv/Scripts/python.exe -I -W error::RuntimeWarning spike_terminal.py
$S/p1812-env/.venv/Scripts/python.exe -I -W error::RuntimeWarning spike_floor.py
```

`spike_terminal.py`:

```
step 10.0 m: bare 149.48 dB
step 10.0 m: 8 m at the Tx point only (index 0) 149.48 dB
step 10.0 m: 8 m over the first 15 m 160.17 dB
step 10.0 m: 10 m everywhere 160.17 dB
step 10.0 m: 10 m blocks, 15 m of every 40 m 160.17 dB
step  1.0 m: bare 149.48 dB
step  1.0 m: 8 m at the Tx point only (index 0) 149.48 dB
step  1.0 m: 8 m over the first 15 m 160.17 dB
step  1.0 m: 10 m everywhere 160.17 dB
step  1.0 m: 10 m blocks, 15 m of every 40 m 160.17 dB
```

`spike_floor.py`, rewritten after the review of 69bcc3b so each line also prints three terms from Py1812's debug log, read from an in-memory buffer: Lbs (troposcatter, equation 44), Ld50 (median diffraction, equation 39) and Lbam (diffraction with ducting, equation 62). The hills cases use ground at 150 m plus 20 m times sin(2 pi x / 2.5 km), hills of plus or minus 20 m.

```
10 km, R 0, 2 m / 2 m: Lb 149.48 dB (Lbs 160.17, Ld50 37.87, Lbam 149.50)
10 km, R 1, 2 m / 2 m: Lb 150.20 dB (Lbs 160.17, Ld50 38.59, Lbam 150.22)
10 km, R 2, 2 m / 2 m: Lb 151.79 dB (Lbs 160.17, Ld50 40.20, Lbam 151.83)
10 km, R 3, 2 m / 2 m: Lb 160.17 dB (Lbs 160.17, Ld50 76.72, Lbam 188.33)
10 km, R 10, 2 m / 2 m: Lb 160.17 dB (Lbs 160.17, Ld50 94.77, Lbam 206.36)
10 km, 8 m over first 15 m only, 10 m step: Lb 160.17 dB (Lbs 160.17, Ld50 62.07, Lbam 173.70)
10 km, 8 m over first 15 m only, 1 m step: Lb 160.17 dB (Lbs 160.17, Ld50 72.21, Lbam 183.83)
10 km, 4 m over first 15 m only, 1 m step: Lb 160.17 dB (Lbs 160.17, Ld50 62.55, Lbam 174.18)
10 km, 4 m over 15 m at mid-path, 1 m step: Lb 152.12 dB (Lbs 160.17, Ld50 40.55, Lbam 152.17)
3 km, R 0, 2 m / 2 m: Lb 125.41 dB (Lbs 149.71, Ld50 24.24, Lbam 125.41)
3 km, R 10, 2 m / 2 m: Lb 149.71 dB (Lbs 149.71, Ld50 77.92, Lbam 179.08)
10 km, R 10, 2 m / 10 m gateway: Lb 160.02 dB (Lbs 160.17, Ld50 54.20, Lbam 165.83)
10 km, R 0, 2 m / 10 m gateway: Lb 134.65 dB (Lbs 160.17, Ld50 23.02, Lbam 134.65)
10 km, hills of plus or minus 20 m, R 0, 2 m / 2 m: Lb 151.10 dB (Lbs 188.78, Ld50 39.47, Lbam 151.10)
10 km, hills of plus or minus 20 m, R 10, 2 m / 2 m: Lb 183.08 dB (Lbs 188.78, Ld50 71.61, Lbam 183.24)
```

What it shows:

- Clutter at the terminal point itself changes nothing (149.48 dB with 8 m at index 0). This matches P.1812-8 section 3.2 and `g[0] = h[0]` in Py1812.
- Equation (63) combines Lbs with Lbam as a soft minimum, so Lbs caps Lb from above. Clutter well above a 2 m antenna drives Lbam past Lbs: 10 m along the path, and 4 m or 8 m over the first 15 m at either step, all give 160.17 dB, and a 10 m gateway antenna gives 160.02 dB with 10 m clutter. Run 5 shows where clutter must stand for that to happen.
- Clutter 1 m or less above the antennas, or in a narrow band away from the ends, still counts: 4 m over 15 m at mid-path gives 152.12 dB, 2 m along the whole path 151.79 dB, 1 m 150.20 dB.
- On a path whose terrain alone is line of sight, Lbs depends only on distance, frequency, N0 and p: 160.17 dB at 10 km and 149.71 dB at 3 km in every flat case here.
- Where terrain blocks the path, the cap is higher and clutter still counts. Over the hills, Lbs is 188.78 dB and 10 m of clutter moves Lb from 151.10 to 183.08 dB. Lbs follows the angular distance (P.1812-8 equation 82), built from the path classification and horizon angles of equations 73 to 81, all from terrain heights; Py1812's comments cite these steps by older equation numbers.
- At 1 m and 10 m steps the results are identical in these cases, but only because the cap hides the difference: for 8 m over the first 15 m, Ld50 is 72.21 dB at 1 m and 62.07 dB at 10 m.

## Run 5: where clutter has to stand to reach the cap (`spike_cap_position.py`)

Added after the review of df1038b and extended after the review of 4be3552. Flat paths at 150 m, 2 m antennas, 10 m step, the same import and constants as run 4. Each line prints Lb, Lbs from the debug log and Lbs minus Lb; 0.1 dB or less counts as at the cap.

```
$S/p1812-env/.venv/Scripts/python.exe -I -W error::RuntimeWarning spike_cap_position.py
```

```
10 km path, 3 m clutter in bands (start to end, metres from the transmitter):
  1000 to 9000 m: Lb 154.51 dB, Lbs 160.17 dB, Lbs - Lb 5.66 dB
  2500 to 7500 m: Lb 152.57 dB, Lbs 160.17 dB, Lbs - Lb 7.60 dB
  100 to 9900 m: Lb 160.12 dB, Lbs 160.17 dB, Lbs - Lb 0.05 dB
  0 to 200 m: Lb 158.11 dB, Lbs 160.17 dB, Lbs - Lb 2.06 dB
  0 to 10000 m: Lb 160.17 dB, Lbs 160.17 dB, Lbs - Lb 0.00 dB
10 km path, clutter everywhere except gap_m at each end:
  3 m, gap 0 m: Lb 160.17 dB, Lbs 160.17 dB, Lbs - Lb 0.00 dB
  3 m, gap 50 m: Lb 160.17 dB, Lbs 160.17 dB, Lbs - Lb 0.00 dB
  3 m, gap 100 m: Lb 160.12 dB, Lbs 160.17 dB, Lbs - Lb 0.05 dB
  3 m, gap 200 m: Lb 159.65 dB, Lbs 160.17 dB, Lbs - Lb 0.52 dB
  3 m, gap 300 m: Lb 158.64 dB, Lbs 160.17 dB, Lbs - Lb 1.53 dB
  3 m, gap 500 m: Lb 156.82 dB, Lbs 160.17 dB, Lbs - Lb 3.35 dB
  3 m, gap 1000 m: Lb 154.51 dB, Lbs 160.17 dB, Lbs - Lb 5.66 dB
  3 m, gap 2000 m: Lb 152.95 dB, Lbs 160.17 dB, Lbs - Lb 7.22 dB
  3 m, gap 4000 m: Lb 151.83 dB, Lbs 160.17 dB, Lbs - Lb 8.34 dB
  10 m, gap 0 m: Lb 160.17 dB, Lbs 160.17 dB, Lbs - Lb -0.00 dB
  10 m, gap 50 m: Lb 160.17 dB, Lbs 160.17 dB, Lbs - Lb 0.00 dB
  10 m, gap 100 m: Lb 160.17 dB, Lbs 160.17 dB, Lbs - Lb 0.00 dB
  10 m, gap 200 m: Lb 160.17 dB, Lbs 160.17 dB, Lbs - Lb 0.00 dB
  10 m, gap 300 m: Lb 160.17 dB, Lbs 160.17 dB, Lbs - Lb 0.00 dB
  10 m, gap 500 m: Lb 160.16 dB, Lbs 160.17 dB, Lbs - Lb 0.01 dB
  10 m, gap 1000 m: Lb 160.06 dB, Lbs 160.17 dB, Lbs - Lb 0.11 dB
  10 m, gap 2000 m: Lb 159.14 dB, Lbs 160.17 dB, Lbs - Lb 1.03 dB
  10 m, gap 4000 m: Lb 156.42 dB, Lbs 160.17 dB, Lbs - Lb 3.75 dB
10 km path, clutter only within reach_m of the transmitter:
  3 m, first 20 m: Lb 157.86 dB, Lbs 160.17 dB, Lbs - Lb 2.31 dB
  3 m, first 50 m: Lb 157.91 dB, Lbs 160.17 dB, Lbs - Lb 2.27 dB
  3 m, first 100 m: Lb 157.98 dB, Lbs 160.17 dB, Lbs - Lb 2.19 dB
  3 m, first 200 m: Lb 158.11 dB, Lbs 160.17 dB, Lbs - Lb 2.06 dB
  3 m, first 500 m: Lb 158.46 dB, Lbs 160.17 dB, Lbs - Lb 1.71 dB
  10 m, first 20 m: Lb 160.17 dB, Lbs 160.17 dB, Lbs - Lb 0.00 dB
  10 m, first 50 m: Lb 160.17 dB, Lbs 160.17 dB, Lbs - Lb 0.00 dB
  10 m, first 100 m: Lb 160.17 dB, Lbs 160.17 dB, Lbs - Lb 0.00 dB
  10 m, first 200 m: Lb 160.17 dB, Lbs 160.17 dB, Lbs - Lb 0.00 dB
  10 m, first 500 m: Lb 160.17 dB, Lbs 160.17 dB, Lbs - Lb 0.00 dB
10 km path, 10 m clutter away from the ends:
  10 m over 2.5 to 7.5 km: Lb 158.42 dB, Lbs 160.17 dB, Lbs - Lb 1.75 dB
  10 m, 15 m band at mid-path: Lb 155.42 dB, Lbs 160.17 dB, Lbs - Lb 4.75 dB
  10 m, 15 m band 1 km from the transmitter: Lb 157.33 dB, Lbs 160.17 dB, Lbs - Lb 2.84 dB
3 km and 5 km paths, 10 m clutter except gap_m at each end:
  3 km, gap 0 m: Lb 149.71 dB, Lbs 149.71 dB, Lbs - Lb 0.00 dB
  3 km, gap 300 m: Lb 148.02 dB, Lbs 149.71 dB, Lbs - Lb 1.69 dB
  3 km, gap 500 m: Lb 144.66 dB, Lbs 149.71 dB, Lbs - Lb 5.06 dB
  3 km, gap 1000 m: Lb 139.13 dB, Lbs 149.71 dB, Lbs - Lb 10.59 dB
  5 km, gap 0 m: Lb 154.15 dB, Lbs 154.15 dB, Lbs - Lb 0.00 dB
  5 km, gap 1000 m: Lb 149.69 dB, Lbs 154.15 dB, Lbs - Lb 4.46 dB
```

What it shows, for these synthetic flat paths only:

- On the 10 km path, 10 m of clutter is at the cap over the first 20 m to 500 m alone, and along the whole path with up to 500 m clear at each end. With 1 km clear it is 0.11 dB below (160.06 dB), with 2 km and 4 km 159.14 and 156.42 dB; over 2.5 to 7.5 km 158.42 dB; a 15 m band of it gives 155.42 dB at mid-path and 157.33 dB 1 km from an end.
- 3 m of clutter is at the cap only along the whole path with 100 m or less clear at each end; over the first 20 m to 500 m alone it stays 1.7 to 2.3 dB below.
- Shorter paths come off the cap sooner: on the 3 km path 10 m of clutter with 300 m, 500 m and 1 km clear gives 148.02, 144.66 and 139.13 dB against Lbs 149.71 dB, and on the 5 km path 1 km clear gives 149.69 dB against 154.15 dB.
- So any distance from the ends that keeps clutter off the cap grows with path length. How often real links reach the cap is for the x72y429 run.

## Run 6: the debug log's Lbs against its definitions (`spike_cap_meaning.py`)

Added on 2026-11-15 for Casey's item 2 addition (PR #24 comment 6894674840): a test that fails if the Lbs value read from Py1812's debug log disappears or changes meaning. The script makes the comparisons that test will make (ADR 0006, Consequences). Py1812 comes from the archive of commit 6c9061dd, installed in a scratch uv environment `$S/p1812-archive-env` (`P1812.py` sha256 `de5de35d2cda26677e654f92ab13183f9f9f40b06bb57a2fa46070efeea4ec2e`). Same in-memory import and constants as run 4: DN 39.126, N0 324.511, 915 MHz, p 50, vertical, zone 4, ground at 150 m unless a case gives hills, 10 m step. Each call passes `debug=1` and an `io.StringIO` as `fid_log`. The 3 km case builds its clutter as `spike_cap_position.py` does, from 300 m to 2 700 m inclusive.

Checks, each within 1e-6 dB:

1. Exactly one `Lbs (dB)` line per call, holding a finite number.
2. Lbs against `tl_tropo(dtot, theta, f, p, N0)` called directly, with `theta` from `smooth_earth_heights` and `ae` from `earth_rad_eff(DN)`, as `bt_loss` builds them (`P1812.py` lines 296, 304 to 322 and 460).
3. Lbs against equations 44 and 45 of P.1812-8, written out in the script.
4. The logged Lbc against equation 63 applied to the logged Lbs and Lbam.
5. Lb against equation 69, the larger of the logged Lb0p and Lbc; the location term is 0 at pL = 50 and sigmaL = 0 (lines 468 to 477).

```
mkdir $S/run6 && cd $S/run6
$S/p1812-archive-env/.venv/Scripts/python.exe -I -W error::RuntimeWarning <repo>/docs/adr/0006-spike/spike_cap_meaning.py
```

```
10 km flat, R 10, 2 m antennas: Lb 160.17 dB, Lbs 160.17 dB, Lb0p 111.63 dB, Lbc 160.17 dB
  Lbs lines 1; residuals tl_tropo 1.5e-08, eq 44 1.5e-08, eq 63 1.3e-09, eq 69 1.3e-08
  Lb0p - Lbc -4.9e+01 dB, larger term Lbc; checks within 1e-06 dB: pass
10 km flat, R 0, 2 m antennas: Lb 149.48 dB, Lbs 160.17 dB, Lb0p 111.63 dB, Lbc 149.48 dB
  Lbs lines 1; residuals tl_tropo 1.5e-08, eq 44 1.5e-08, eq 63 5.3e-08, eq 69 4.5e-08
  Lb0p - Lbc -3.8e+01 dB, larger term Lbc; checks within 1e-06 dB: pass
10 km hills of plus or minus 20 m, R 10, 2 m antennas: Lb 183.08 dB, Lbs 188.78 dB, Lb0p 111.63 dB, Lbc 183.08 dB
  Lbs lines 1; residuals tl_tropo 4.0e-08, eq 44 4.0e-08, eq 63 5.1e-08, eq 69 3.3e-08
  Lb0p - Lbc -7.1e+01 dB, larger term Lbc; checks within 1e-06 dB: pass
3 km flat, R 10 with 300 m clear at each end, 2 m antennas: Lb 148.02 dB, Lbs 149.71 dB, Lb0p 101.17 dB, Lbc 148.02 dB
  Lbs lines 1; residuals tl_tropo 3.3e-08, eq 44 3.3e-08, eq 63 2.6e-08, eq 69 1.0e-08
  Lb0p - Lbc -4.7e+01 dB, larger term Lbc; checks within 1e-06 dB: pass
1 km flat, R 0, 10 m antennas: Lb 91.63 dB, Lbs 140.17 dB, Lb0p 91.63 dB, Lbc 91.63 dB
  Lbs lines 1; residuals tl_tropo 3.7e-08, eq 44 3.7e-08, eq 63 4.2e-10, eq 69 1.3e-09
  Lb0p - Lbc 0.0e+00 dB, larger term Lb0p; checks within 1e-06 dB: pass
largest residual 5.25e-08 dB; all checks pass: True
files written to the working folder: []
```

What it shows:

- Every call writes exactly one `Lbs (dB)` line, and with a log object passed nothing is written to the working folder. At 6c9061dd `bt_loss` opens a log file in the working folder only when no log is passed (lines 243 to 256), and it never closes a log it is passed.
- The logged Lbs equals the direct `tl_tropo` call and equations 44 and 45 to 4.0e-8 dB or less. The logged Lbc equals equation 63, and Lb equals equation 69, to 5.3e-8 dB or less. The largest residual, 5.25e-8 dB, is the equation 63 check on the bare 10 km path. All are inside 1e-6 dB, and their size is the `%.10g` rounding of the log (line 258).
- On the 1 km path with 10 m antennas, Lb is Lb0p, the first term of equation 69: the path is short and clear, so P.1812 returns its line-of-sight loss. Lbc equals Lb0p to the log's ten significant digits there, so this case shows equation 69 returning Lb0p but does not separate the two terms. On the other four paths Lbc is the larger term, by 38 to 71 dB.
- The 3 km case repeats run 5's 148.02 dB.

## Install form checks

- Git source without git: with `git` removed from `PATH` and an empty uv cache, the `git+https` install fails with `Git executable not found. Ensure that Git is installed and available.` The local `mesh-terrain-dagster-webserver` image has no `git` (`command -v git` empty), so option 1.1 needs git added to the image.
- Archive source: `uv pip install "py1812 @ https://github.com/eeveetza/Py1812/archive/6c9061dd022e93c84169059a016643926ce89964.tar.gz"` installs the same `py1812==8.0`. In a scratch project, `uv lock` records it as `source = { url = "..." }` with `sdist = { hash = "sha256:03d7c5bd..." }`, the hash of a direct `curl` download of the same URL.
- Py1812's `pyproject.toml` builds with `flit_core >=3.2,<4` and depends on numpy only. The archive also carries upstream's `tests/validateP1812.py` and ITU validation profiles under `tests/validation_profiles/`; they were not run here. `validateP1812.py` passes each profile's own DN and N0 to `bt_loss` (lines 247 to 248), so the profiles need no maps. They come from ITU-R Study Group 3's measurement database, and their licence is unchecked.

## Not done

- No Docker build: nothing here changes an image, and the image steps belong to platform-eng once Casey decides.
- No change to `pyproject.toml` or `uv.lock`; the lock test used a scratch project.
- No lidar, EPT, DEM or canopy download.
