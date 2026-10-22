# Spike image for ADR 0003. Not the ingester image; ingest-eng builds that in
# Phase 1 proper. One image runs both the capture (default command) and the
# analysis (override the command). The pins are spike-only and do not enter
# pyproject.toml or uv.lock.
#
# Build, capture, analyse:
#   docker build -t mesh-spike docs/adr/0003-spike
#   docker run -d --name mesh-spike-capture --restart unless-stopped \
#     -v C:/Users/Casey/mesh-terrain-data/spike-capture:/data \
#     -e MQTT_HOST=mqtt.meshtastic.org -e MQTT_USER=meshdev \
#     -e MQTT_PASSWORD=large4cats -e MQTT_TOPICS=msh/US/# mesh-spike
#   The analysis mounts the committed scripts over the image's copies; the
#   full command and the boundary files are in README.md.

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
