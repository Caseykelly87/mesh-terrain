# PostgreSQL 17 with PostGIS 3.5 plus the h3-pg extension, cloned at a tag and
# checked against a pinned commit; the pairing matches RDS for PostgreSQL 17
# (ADR 0004). The Debian package (postgresql-17-h3) would be quicker, but the
# apt repository drops old builds when it publishes new ones, so a source
# build checked against a commit is the only pin that holds.
FROM postgis/postgis:17-3.5@sha256:01a6a70e41e6c4467c8f55f6063555ed72db2d6662cd0d571040d42eadaeb6f6 AS builder

ARG H3_PG_TAG=v4.2.3
# The commit the tag pointed at when it was pinned; a moved tag fails the build.
ARG H3_PG_COMMIT=a26630b8353d441e6bc8065c0a8dcaa3d89ef87b

# The Debian variant of this image is still bullseye, which both Debian and
# the PostgreSQL apt project have moved to their archive hosts; the live
# mirrors return 404 for its packages. Only the builder stage installs
# packages, so the final image is untouched.
RUN sed -i 's|http://apt.postgresql.org/pub/repos/apt/|http://apt-archive.postgresql.org/pub/repos/apt/|' \
        /etc/apt/sources.list.d/*.list \
    && sed -i 's|http://deb.debian.org/debian-security|http://archive.debian.org/debian-security|; s|http://deb.debian.org/debian |http://archive.debian.org/debian |' \
        /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
        postgresql-server-dev-17 \
    && rm -rf /var/lib/apt/lists/*

# cmake downloads the H3 core library tarball at configure time and verifies
# its sha256 against the value pinned inside h3-pg, so the commit pins both.
# postgresql-server-dev-17 ships pgindent, and h3-pg runs it over its sources
# before every build when it finds one; pgindent fails without a typedefs
# file, so the lookup is pre-empted with a not-found value.
RUN git clone --depth 1 --branch "${H3_PG_TAG}" https://github.com/postgis/h3-pg.git /tmp/h3-pg \
    && test "$(git -C /tmp/h3-pg rev-parse HEAD)" = "${H3_PG_COMMIT}" \
    && cmake -S /tmp/h3-pg -B /tmp/h3-pg/build -DCMAKE_BUILD_TYPE=Release \
        -DPostgreSQL_INDENT=PostgreSQL_INDENT-NOTFOUND \
    && cmake --build /tmp/h3-pg/build \
    && cmake --install /tmp/h3-pg/build --component h3-pg

FROM postgis/postgis:17-3.5@sha256:01a6a70e41e6c4467c8f55f6063555ed72db2d6662cd0d571040d42eadaeb6f6

COPY --from=builder /usr/lib/postgresql/17/lib/h3*.so /usr/lib/postgresql/17/lib/
COPY --from=builder /usr/share/postgresql/17/extension/h3* /usr/share/postgresql/17/extension/
COPY initdb/ /docker-entrypoint-initdb.d/
