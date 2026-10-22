#!/bin/sh
# SPLAT point-to-point check for the ADR 0002 spike. No SDF tile is present,
# so SPLAT assumes sea level; -olditm selects Longley-Rice over ITWOM and -N is
# deliberately absent because it suppresses the path loss report.
set -e
splat -t tx.qth -r rx.qth -metric -olditm -d /opt/spike
cat tx-to-rx.txt
