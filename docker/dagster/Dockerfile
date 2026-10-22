# One image for dagster-webserver and dagster-daemon; the Compose file picks
# the command. Built from the repository root so the lock file and packages
# are the same ones a developer runs on the host.
FROM python:3.12-slim-bookworm

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

COPY docker/dagster/dagster.yaml docker/dagster/workspace.yaml ${DAGSTER_HOME}/
