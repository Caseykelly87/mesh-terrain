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

## Run 2: placeholder import, DN and N0 passed (`spike_placeholder.py`)

This is the import that option 2.2 recommends. The script writes a placeholder `P1812.npz` holding two 1 by 1 grids of NaN, no ITU data. It refuses to go on if `Py1812.P1812` is already imported or the installed package holds an npz, then imports Py1812 with the map load pointed at the placeholder. It reads the two ITU text files with `np.loadtxt`, takes the path centre from Py1812's `great_circle_path` and interpolates DN and N0 with Py1812's `interp2`. Finally it makes the flat-path call with them, and once more without them.

```
placeholder npz: 518 bytes
import with placeholder: ok, DN50 shape (1, 1)
path centre 38.63001 N -90.19244 E: DN 39.126, N0 324.511
DN and N0 passed: Lb 149.48 dB  Ep 49.11 dBuV/m
DN and N0 omitted: IndexError: index 34 is out of bounds for axis 0 with size 1
```

The placeholder satisfies the import. The real maps enter only through the two numbers passed on each call, and a call that forgets them fails instead of returning a number built on the placeholder. The installed package folder still held no npz afterwards.

## Install form checks

- Git source without git: with `git` removed from `PATH` and an empty uv cache, the `git+https` install fails with `Git executable not found. Ensure that Git is installed and available.` The local `mesh-terrain-dagster-webserver` image has no `git` (`command -v git` empty), so option 1.1 needs git added to the image.
- Archive source: `uv pip install "py1812 @ https://github.com/eeveetza/Py1812/archive/6c9061dd022e93c84169059a016643926ce89964.tar.gz"` installs the same `py1812==8.0`. In a scratch project, `uv lock` records it as `source = { url = "..." }` with `sdist = { hash = "sha256:03d7c5bd..." }`, the hash of a direct `curl` download of the same URL.
- Py1812's `pyproject.toml` builds with `flit_core >=3.2,<4` and depends on numpy only. The archive also carries upstream's `tests/validateP1812.py` and ITU validation profiles under `tests/validation_profiles/`; they were not run here. `validateP1812.py` passes each profile's own DN and N0 to `bt_loss` (lines 247 to 248), so the profiles need no maps. They come from ITU-R Study Group 3's measurement database, and their licence is unchecked.

## Not done

- No Docker build: nothing here changes an image, and the image steps belong to platform-eng once Casey decides.
- No change to `pyproject.toml` or `uv.lock`; the lock test used a scratch project.
- No lidar, EPT, DEM or canopy download.
