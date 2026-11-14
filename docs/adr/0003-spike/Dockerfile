# Spike analysis image for ADR 0003. The pins are spike-only and do not enter
# pyproject.toml or uv.lock. The capture command (capture.py) now runs from the
# repository with uv and the ingester package, never in this image and never
# under a restart policy (ADR 0005); this image runs analyze.py only.
#
# Build: docker build -t mesh-spike docs/adr/0003-spike
# The analysis commands are in README.md.

FROM python:3.12-slim-bookworm

RUN pip install --no-cache-dir \
    meshtastic==2.7.11 \
    cryptography==50.0.2 \
    structlog==26.1.0 \
    shapely==2.2.0 \
    pyproj==3.8.0

WORKDIR /opt/spike
COPY analyze.py ./
ENV PYTHONUNBUFFERED=1
CMD ["python", "analyze.py", "--help"]
