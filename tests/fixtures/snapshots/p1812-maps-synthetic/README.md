# p1812-maps-synthetic

A synthetic stand-in for the ITU-R P.1812 digital maps, for tests and the CI
engine fixture run (ADR 0006 decision 2). It holds no ITU data.

- `DN50.TXT`: 121 rows of 241 values, every value 40.0 (N-units/km).
- `N050.TXT`: 121 rows of 241 values, every value 325.0 (N-units).

The grid is the shape of the real maps (1.5 degree spacing, latitude 90 to -90
by row, longitude 0 to 360 by column), so Py1812's `interp2` reads it the same
way, but the values are made-up constants chosen near the St. Louis values.
Any P.1812 number computed from this snapshot is not a real prediction, and a
run manifest that names `p1812-maps-synthetic` says so.

Each file is plain text, values separated by one space, LF line endings.
The real maps come from `make p1812-maps` into the snapshot
`itu-r-p1812-8-202509` and are never committed.
