# Spike image for ADR 0003. Not the ingester image; ingest-eng builds that in
# Phase 1 proper. One image runs both the capture (default command) and the
# analysis (override the command). The pins are spike-only and do not enter
# pyproject.toml or uv.lock.
#
# Build: docker build -t mesh-spike docs/adr/0003-spike
# The capture and analysis commands are in README.md. A capture always runs
# with --restart on-failure and END_AT, never --restart unless-stopped.

FROM python:3.12-slim-bookworm

RUN pip install --no-cache-dir \
    paho-mqtt==2.1.0 \
    meshtastic==2.7.11 \
    cryptography==50.0.2 \
    structlog==26.1.0 \
    shapely==2.2.0 \
    pyproj==3.8.0

WORKDIR /opt/spike
COPY capture.py analyze.py ./
ENV PYTHONUNBUFFERED=1
CMD ["python", "capture.py"]
