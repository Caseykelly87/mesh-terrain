# ADR 0002: Call the NTIA ITM library through ctypes for the terrain propagation model

Date: 2026-10-22
Status: accepted
Proposed by: terrain-rf-eng
Decided by: Casey

## Context

Phase 3 of `docs/PLAN.md` predicts how much signal a 915 MHz LoRa link loses between two nodes, so that Phase 4 can compare the prediction with what gateways observed. The plan names three model tiers and leaves the implementation of the third open. Four terms, defined once:

- Free-space loss is the signal loss over a path with nothing in the way, set only by distance and frequency. At 915 MHz over 10 km it is 111.7 dB. It is the floor every other model adds to.
- Knife-edge diffraction is the extra loss when a ridge or building edge sticks up into the path. ITU-R P.526 gives a closed formula from how far the edge rises above the direct line. We write this ourselves.
- ITM, the Irregular Terrain Model (also called Longley-Rice), is the model the United States government has used since 1968 to predict loss over real terrain profiles. It takes the ground elevations along the path, the antenna heights, frequency, ground electrical constants and climate, and returns a loss in dB. NTIA publishes the reference C++ code.
- SPLAT is a GPL command-line program for radio planning that contains its own copy of the ITM code plus a successor model called ITWOM. It reads terrain from its own tile format (SDF) and writes text reports.

The Phase 0 exit criterion asks for this choice to be recorded. The spike in `docs/adr/0002-itm-spike/` built both candidates on `python:3.12-slim-bookworm`, the base platform-eng uses for the Dagster image, within 19 minutes of a three-hour box. Both work. The numbers on one flat 10 km path with 2 m antennas: free space 111.7 dB, NTIA library 151.8 dB, SPLAT's Longley-Rice 151.4 dB.

## Options

1. NTIA ITM library compiled into `libitm.so` and called from Python through the standard library `ctypes` module. Advantage: the reference implementation, public domain, in-process, takes a C double array and returns in microseconds; the compile is one `g++` line, 10 s, and a 44 KB file. Cost: NTIA ships no Linux build system or Python bindings, so the Dockerfile carries a `sed` that fixes Windows include paths and a compiler flag that removes `__declspec`, and the `ctypes` signature for `ITM_P2P_CR_Ex` lives in our code and must match `include/itm.h` exactly.
2. SPLAT 1.4.2 from Debian apt, wrapped as a subprocess. Advantage: one `apt-get install`, no compiler, and SPLAT's reports also give Fresnel and obstruction figures for free. Cost: the wrapper has to write SDF terrain tiles from our DEM (one file per degree square, in SPLAT's own text layout), write `.qth` and `.lrp` files per link, run the binary once per profile (tens of thousands per nightly run, each a process start and a tile parse), and scrape the loss out of a text report; SPLAT's default is ITWOM, so every call needs `-olditm`, and `-N` must not be used because it suppresses the report. SPLAT is GPL version 2 or later and this project is MIT: calling a separate binary over files is fine, linking its code into our process or copying its source is not, and the image would ship GPL software alongside our code. The wrapper would cost more code (tile writer, file I/O, report parser) to reach the same number more slowly than option 1. It adds 137 MB to the image, almost all of it the `gnuplot-qt` package that `splat` depends on and that we would never run.
3. Neither. Ship free-space plus knife-edge and Okumura-Hata as the headline models, per the plan's cut order item 2. Advantage: no native code, nothing to wrap. Cost: the hypothesis in the plan names ITM, the published 10 to 23 dB RMSE comparisons are ITM comparisons, and the knife-edge versus ITM sanity check in Phase 3 goes away.

Third-party Python ports (`itmlogic`, `pyitm`) were seen and not pursued: they are dependencies outside the stack table, `pyitm` is area mode only, and a pure Python point-to-point loop would be slower than the C++ library by orders of magnitude.

## Decision

Option 1, because the library builds in seconds, matches SPLAT's own Longley-Rice to 0.4 dB and has no licence friction.

## Consequences

- platform-eng adds a build stage to the Dagster image: `g++`, `curl`, `ca-certificates`, download the pinned NTIA tarball, verify its sha256, apply the include fix, compile, and copy only `/usr/local/lib/libitm.so` into the runtime stage. The runtime needs `libstdc++6`, which the base already carries. Image growth is about 44 KB.
- terrain-rf-eng owns `terrain/itm.py`: the `ctypes` declarations, a `pfl` array builder from the profile sampler, the climate and ground constants as named defaults, and a test that reproduces the 151.76 dB flat-path figure from the spike plus a comparison against the `cmd_examples` outputs in the NTIA repository.
- The `sed` and the `__declspec` flag are modifications to NTIA code and must stay recorded in the Dockerfile and `docs/adr/0002-itm-spike/README.md` with their date, as the NTIA licence asks. NTIA is acknowledged in the `terrain/` README.
- If NTIA publishes a Linux build or Python package later, the `ctypes` layer is the only thing to replace.
- If Casey chooses option 2 instead, the SDF tile writer and report parser become Phase 3 work and the plan's performance note on process pools applies from the start. If option 3, `link_prediction.itm_db` is dropped from the features schema and the Phase 4 baseline table loses one row.
