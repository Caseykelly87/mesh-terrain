"""Reference figures for the reproduction gate: the spike's analysis rerun on the real-id archive.

Runs the seven spike runs of README.md through analyze.py, unchanged, on the
2026-10-22 archive, MSA only, and writes for each run analyze.py's full output
and the headline figures that `reproduce_from_snapshot.py` later computes from
the snapshot. Both scripts compute the figures with `figures.headline`, and
this script checks them against the headline table analyze.py itself prints,
so the reference is the spike's analysis and not a reimplementation.

`--without-fragment` leaves out `capture-2026-10-28T20.jsonl`, the 4-minute
public fragment from the unintended restart, which the snapshot does not hold;
every other file is read as the spike read it. The MeshSTL folder's own
2026-10-28T20 fragment is read in both variants, and every one of its records
falls outside the runs' `--start` and `--end` window, so analyze.py skips it.

Each tally is loaded once and copied for every use, so a variant reads the
public archive four times (full, two topic-root subsets, shared 24 hours) and
the MeshSTL folder once. Output, under OUT_DIR, is aggregates only: the run
outputs and `reference-<variant>.json`. It never prints or writes an id, a
topic, a coordinate or a payload.

Usage, from the repository root, Git Bash:

    uv run --frozen --with shapely==2.2.0 --with pyproj==3.8.0 python -I \
        docs/adr/0003-spike/reference_from_archive.py OUT_DIR [--without-fragment]
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import figures  # noqa: E402
from figures import RUNS, az  # noqa: E402

DATA = Path("C:/Users/Casey/mesh-terrain-data")
FRAGMENT = "capture-2026-10-28T20.jsonl"
DECRYPT_REASONS = ("wrong_key", "pki", "zero_id_or_from")
INTERNET_COPY = "e, internet copy (via_mqtt)"


class Archive:
    """Tallies loaded once per (broker, window, roots) through analyze.load_capture."""

    def __init__(self, dirs: dict[str, Path], without_fragment: bool) -> None:
        self.dirs = dirs
        self.load_capture = az.load_capture
        self.cache: dict[tuple[Any, ...], Any] = {}
        self.files_read: dict[str, int] = {}
        if without_fragment:
            original = az.capture_paths
            public = dirs["public"].resolve()

            def capture_paths(data_dir: Path) -> list[str]:
                paths = original(data_dir)
                if Path(data_dir).resolve() != public:
                    return paths
                return [path for path in paths if Path(path).name != FRAGMENT]

            az.capture_paths = capture_paths

    def broker_of(self, data_dir: Any) -> str:
        found = Path(data_dir).resolve()
        return next(name for name, path in self.dirs.items() if path.resolve() == found)

    def load(self, broker: str, start_s: float, end_s: float, roots: Any) -> Any:
        key = (broker, start_s, end_s, roots)
        if key not in self.cache:
            started = time.monotonic()
            self.files_read[broker] = len(az.capture_paths(self.dirs[broker]))
            self.cache[key] = self.load_capture(self.dirs[broker], start_s, end_s, roots)
            print(f"loaded {broker} in {time.monotonic() - started:.0f} s", file=sys.stderr)
        return copy.deepcopy(self.cache[key])


def run_main(archive: Archive, name: str) -> str:
    """analyze.main for one run, with its loader served from the archive's cache."""
    broker, start, end, roots, borrow = RUNS[name]
    az.load_capture = lambda data_dir, start_s, end_s, roots=None: archive.load(
        archive.broker_of(data_dir), start_s, end_s, roots
    )
    argv = [str(figures.SPIKE_DIR / "analyze.py"), str(archive.dirs[broker]), str(figures.MSA_CSV)]
    argv += ["--start", start, "--end", end] if start and end else []
    argv += ["--roots", roots] if roots else []
    argv += ["--positions-from", str(archive.dirs[borrow])] if borrow else []
    sys.argv = argv
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        az.main()
    return buffer.getvalue()


def counts(tally: Any) -> dict[str, int]:
    """Decrypt failures by the spike's reason, TEXT_MESSAGE_APP envelopes and internet copies."""
    found = {
        f"decrypt_failed: {reason}": tally.counts[("reason", f"decrypt_failed: {reason}")]
        for reason in DECRYPT_REASONS
    }
    found["TEXT_MESSAGE_APP envelopes"] = tally.counts[("port", "1")]
    found["internet copies"] = sum(
        count
        for key, count in tally.counts.items()
        if key[0] == "volume_records" and key[2] == INTERNET_COPY
    )
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--without-fragment", action="store_true")
    parser.add_argument("--public", type=Path, default=DATA / "spike-capture")
    parser.add_argument("--meshstl", type=Path, default=DATA / "meshstl-capture")
    args = parser.parse_args()
    variant = "without-fragment" if args.without_fragment else "with-fragment"
    out_dir = args.out_dir / variant
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = Archive({"public": args.public, "meshstl": args.meshstl}, args.without_fragment)
    runs: dict[str, Any] = {}
    for name in RUNS:
        text = run_main(archive, name)
        (out_dir / f"{name}.txt").write_text(text, encoding="utf-8")
        found = figures.headline(archive.load, name)
        runs[name] = {
            "figures": found,
            "differs_from_printed_table": figures.matches_printed(
                found, figures.printed_headline(text)
            ),
        }
    whole = {
        "public": archive.load("public", *figures.WHOLE, None),
        "meshstl": archive.load("meshstl", *figures.window_s(figures.SHARED), None),
    }
    reference = {
        "variant": variant,
        "files_read": archive.files_read,
        "runs": runs,
        "counts": {broker: counts(tally) for broker, tally in whole.items()},
    }
    path = args.out_dir / f"reference-{variant}.json"
    path.write_text(json.dumps(reference, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(reference, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
