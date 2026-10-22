# One image for dagster-webserver and dagster-daemon; the Compose file picks
# the command. Built from the repository root so the lock file and packages
# are the same ones a developer runs on the host.
#
# The base is pinned by digest because the ITM figure that terrain/itm.py
# tests against (151.76 dB on the ADR 0002 flat path) was measured on this
# exact image and compiler; a silent base update could move it.
ARG PYTHON_BASE=python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70

# Stage itm-build compiles the NTIA Irregular Terrain Model (ITM) C++ sources
# into a shared library that terrain/itm.py loads through ctypes (ADR 0002).
# NTIA ships no Linux build system, so this is the whole build. The compiler
# and curl stay in this stage; the runtime stage copies only the .so.
#
# The ITM source is public domain software from the National
# Telecommunications and Information Administration (NTIA), Institute for
# Telecommunication Sciences. NTIA asks that derivatives acknowledge NTIA and
# record the date and nature of any modification; the two edits below are
# listed with their dates and repeated in docs/adr/0002-itm-spike/README.md.
FROM ${PYTHON_BASE} AS itm-build

# master at 2024-09-24, after the last tagged release (v1.4, 2021), because
# it carries the const-correctness fix.
ARG ITM_COMMIT=183ad95bd813a8be11009df396e1c631356864b2
ARG ITM_SHA256=42db17607994811977b1aa2e6a7fd0f062e0b28bc7c14285c64073292814a093

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/itm-src
RUN curl -fsSL -o itm.tar.gz "https://codeload.github.com/NTIA/itm/tar.gz/${ITM_COMMIT}" \
    && echo "${ITM_SHA256}  itm.tar.gz" | sha256sum -c - \
    && tar xzf itm.tar.gz --strip-components=1 \
    && rm itm.tar.gz

# Two Windows-isms block a Linux build.
# Modification 2026-10-21: the sources include headers as "..\include\Name.h";
# GCC reads the backslashes as part of the file name, so the sed rewrites those
# include lines to "Name.h".
# Modification 2026-10-21: itm.h defines DLLEXPORT as
# extern "C" __declspec(dllexport) with no non-Windows branch; defining
# __declspec away on the compiler line keeps the extern "C" linkage.
# The nm check fails the build if the point-to-point entry points did not
# export, which is the one thing the ctypes caller needs.
RUN sed -i 's|#include "..\\include\\\([A-Za-z0-9_]*\.h\)"|#include "\1"|' src/*.cpp \
    && g++ -O2 -fPIC -shared -std=c++17 '-D__declspec(x)=' -Iinclude src/*.cpp \
    -o /usr/local/lib/libitm.so \
    && test "$(nm -D /usr/local/lib/libitm.so | grep -c ' T ITM_P2P')" -eq 4

FROM ${PYTHON_BASE}

COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    DAGSTER_HOME=/opt/dagster/home

WORKDIR /opt/mesh-terrain

# Dependencies first so a code change does not invalidate the package layer.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY pipeline/ pipeline/
COPY terrain/ terrain/
COPY dbt/ dbt/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# dbt packages are installed at build time so the code location loads without
# network access; the manifest itself is parsed at load time (see
# pipeline/definitions.py for why).
RUN uv run --frozen --no-dev dbt deps --project-dir dbt --profiles-dir dbt

ENV PATH="/opt/mesh-terrain/.venv/bin:${PATH}"

# The library needs only libstdc++, which the base image already carries.
# Copied last so an ITM change does not invalidate the package layers.
COPY --from=itm-build /usr/local/lib/libitm.so /usr/local/lib/libitm.so

COPY docker/dagster/dagster.yaml docker/dagster/workspace.yaml ${DAGSTER_HOME}/
