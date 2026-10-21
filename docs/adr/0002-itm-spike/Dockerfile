# Spike image for ADR 0002. Not the Dagster image; platform-eng owns that.
# Stage "itm" compiles the NTIA ITM C++ sources into a shared library on the
# same base the Dagster image uses and runs check_itm.py against it through
# ctypes. Stage "splat" installs the Debian splat package for comparison.
#
# Build and run:
#   docker build --target itm -t itm-spike docs/adr/0002-itm-spike
#   docker run --rm itm-spike
#   docker build --target splat -t splat-spike docs/adr/0002-itm-spike
#   docker run --rm splat-spike

FROM python:3.12-slim-bookworm AS itm

ARG ITM_COMMIT=183ad95bd813a8be11009df396e1c631356864b2
ARG ITM_SHA256=42db17607994811977b1aa2e6a7fd0f062e0b28bc7c14285c64073292814a093

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The source lives in its own directory, separate from the spike scripts.
WORKDIR /opt/itm-src
RUN curl -fsSL -o itm.tar.gz "https://codeload.github.com/NTIA/itm/tar.gz/${ITM_COMMIT}" \
    && echo "${ITM_SHA256}  itm.tar.gz" | sha256sum -c - \
    && tar xzf itm.tar.gz --strip-components=1 \
    && rm itm.tar.gz

# Two Windows-isms block a Linux build. The source files include headers as
# "..\include\Name.h" with backslashes, which GCC treats as a literal file
# name; the sed rewrites those include lines to "Name.h" (a source
# modification, as the NTIA licence asks us to record). itm.h defines
# DLLEXPORT as extern "C" __declspec(dllexport) with no non-Windows branch;
# defining __declspec away on the compiler line keeps the extern "C" linkage.
RUN sed -i 's|#include "..\\include\\\([A-Za-z]*\.h\)"|#include "\1"|' src/*.cpp \
    && g++ -O2 -fPIC -shared -std=c++17 '-D__declspec(x)=' -Iinclude src/*.cpp \
    -o /usr/local/lib/libitm.so \
    && nm -D /usr/local/lib/libitm.so | grep -c ' T ITM_P2P'

WORKDIR /opt/spike
COPY check_itm.py .
CMD ["python", "check_itm.py"]

FROM python:3.12-slim-bookworm AS splat

RUN apt-get update \
    && apt-get install -y --no-install-recommends splat \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/spike
CMD ["splat"]
