# ITM spike for ADR 0002

Evidence for `docs/adr/0002-propagation-model.md`. Run on 2026-10-21 on Docker 29.2.1 (Linux engine) from the `python:3.12-slim-bookworm` base that the Dagster image uses. Nothing here is production code.

## What was tried

1. Official Python bindings from NTIA: none exist. The NTIA/itm repository ships C++ sources, a Visual Studio 2019 project and a .NET wrapper on NuGet. No CMake, no Makefile, no Python. The PyPI packages `pyitm` (area mode only) and `itmlogic` (pure Python port of the 1.2.2 FORTRAN) are third-party ports, not NTIA code, and are outside the plan's stack. Not pursued.
2. Shared library plus `ctypes`: compile `src/*.cpp` with `g++` into `libitm.so` and call `ITM_P2P_CR_Ex` from the standard library `ctypes` module. This worked. See below.
3. SPLAT from Debian bookworm apt: installs and runs a point-to-point calculation without terrain tiles. See below.

## Source

- Repository: NTIA/itm, branch `master`, commit `183ad95bd813a8be11009df396e1c631356864b2` (2024-09-24, "Merge pull request #12 from aromanielloNTIA/const-correctness"). Tagged releases stop at v1.4 (2021-04-26); the commit is pinned instead because it carries the const-correctness fix.
- Tarball `https://codeload.github.com/NTIA/itm/tar.gz/<commit>`, sha256 `42db17607994811977b1aa2e6a7fd0f062e0b28bc7c14285c64073292814a093`, 628 KB unpacked.
- Licence: NTIA software disclaimer, public domain in the United States, royalty-free elsewhere. Conditions: acknowledge NTIA in copies and derivatives; state the date and nature of any modification; no warranty.

## Modifications to the NTIA sources

Two changes are made at build time by the Dockerfile; the tarball itself is untouched.

- 2026-10-21: every `#include "..\include\Name.h"` in `src/*.cpp` is rewritten to `#include "Name.h"`. GCC reads a backslash as part of the file name.
- 2026-10-21: `__declspec(x)` is defined as empty on the compiler command line so that the `DLLEXPORT` macro in `include/itm.h` reduces to `extern "C"`.

## Commands and timings

```
docker build --target itm -t itm-spike docs/adr/0002-itm-spike
docker run --rm itm-spike
docker build --target splat -t splat-spike docs/adr/0002-itm-spike
```

| Step | Wall clock | Result |
| --- | --- | --- |
| ITM stage, first build (apt toolchain, download, compile) | 69 s to the compile error; 24 s cached rebuild after the fix | compile itself 10.3 s |
| ITM stage, image size | 131.2 MB against 45.3 MB base | `libitm.so` is 43.9 KB; the rest is `g++`, `curl` and their dependencies |
| SPLAT stage, fresh build | 97 s | image 182.6 MB; `splat 1.4.2-3` itself is 553 KB, the rest is its `gnuplot` dependency, which apt resolves to `gnuplot-qt` |
| SPLAT apt install alone | 55 s | exit 0 |

Compiler: `g++ (Debian 12.2.0-14+deb12u1) 12.2.0`. The build exports four point-to-point symbols (`nm -D` count of ` T ITM_P2P`).

## check_itm.py output

Flat 10 km path at sea level, 1001 samples at 10 m, 915 MHz, both antennas 2 m above ground (assumed), climate 5 (continental temperate), N0 301, vertical polarisation, epsilon 15, sigma 0.005 S/m, mdvar 12, confidence 50, reliability 50.

```
itm return code: 0 (0 is success, see ERRORS_AND_WARNINGS.md)
itm warnings flags: 0x0
path: 10.0 km flat at 10 m, 1001 samples
antennas: 2.0 m above ground at both ends (assumed)
itm propagation mode: 1 (1 line of sight, 2 diffraction, 3 troposcatter)
itm internal free-space loss: 111.68 dB
itm reference attenuation: 40.13 dB
free-space loss, closed form: 111.68 dB
itm basic transmission loss: 151.76 dB
itm minus free space: +40.08 dB
```

The 40 dB above free space is what two antennas 2 m above flat ground should show at 10 km: the ground-reflected ray almost cancels the direct ray, and the two-ray closed form gives 40 log10(10000) minus 20 log10(2 x 2), about 148 dB, before ITM's smooth-earth correction.

## SPLAT output

Inputs, committed beside the Dockerfile: `tx.qth` and `rx.qth`, site files at 38.6 N, 90.2 W and 90.08506 W (SPLAT reports 9.99 km) with 2 m antennas, and `tx.lrp` with the same ground, climate, frequency, polarisation and 50 percent fractions. No `.sdf` tile is present. `check_splat.sh` runs `splat -t tx.qth -r rx.qth -metric -olditm -d /opt/spike` and prints the report; `docker run --rm splat-spike` reproduces it.

SPLAT printed `Region "38:39:90:91" assumed as sea-level` and wrote `tx-to-rx.txt`:

```
Free space path loss: 111.69 dB
Longley-Rice path loss: 151.40 dB
Attenuation due to terrain shielding: 39.71 dB
Mode of propagation: Line-Of-Sight Mode
Longley-Rice model error number: 0 (No error)
```

Without `-olditm` SPLAT uses its default ITWOM 3.0 model and reports 133.50 dB on the same path; that is a different model, not an ITM result. Passing `-N` suppresses the path loss report, so a wrapper must not use it.

The NTIA library and SPLAT's Longley-Rice agree to 0.36 dB on this path; the remaining difference is SPLAT's 9.99 km distance and its own profile sampling.

## Time spent

| Step | Minutes |
| --- | --- |
| 1. Locate source, licence, bindings | 8 |
| 2. Build library, two include fixes, run check | 4 |
| 3. SPLAT install, image, two runs | 7 |
| Total of the three-hour box | 19 |
