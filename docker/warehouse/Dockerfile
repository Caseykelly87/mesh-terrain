# PostGIS 16-3.4 plus the h3-pg extension built from a pinned release tag.
# The Debian package (postgresql-16-h3) would be quicker, but the apt
# repository drops old builds when it publishes new ones, so a source build
# at a tag is the only pin that holds.
FROM postgis/postgis:16-3.4 AS builder

ARG H3_PG_TAG=v4.2.3
# The commit the tag pointed at when it was pinned; a moved tag fails the build.
ARG H3_PG_COMMIT=a26630b8353d441e6bc8065c0a8dcaa3d89ef87b

# The 16-3.4 image is Debian bullseye, which both Debian and the PostgreSQL apt
# project have moved to their archive hosts; the live mirrors return 404 for
# its packages. Only the builder stage installs packages, so the final image
# is untouched.
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
        postgresql-server-dev-16 \
    && rm -rf /var/lib/apt/lists/*

# cmake downloads the H3 core library tarball at configure time and verifies
# its sha256 against the value pinned inside h3-pg, so the tag pins both.
RUN git clone --depth 1 --branch "${H3_PG_TAG}" https://github.com/postgis/h3-pg.git /tmp/h3-pg \
    && test "$(git -C /tmp/h3-pg rev-parse HEAD)" = "${H3_PG_COMMIT}" \
    && cmake -S /tmp/h3-pg -B /tmp/h3-pg/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/h3-pg/build \
    && cmake --install /tmp/h3-pg/build --component h3-pg

FROM postgis/postgis:16-3.4

COPY --from=builder /usr/lib/postgresql/16/lib/h3*.so /usr/lib/postgresql/16/lib/
COPY --from=builder /usr/share/postgresql/16/extension/h3* /usr/share/postgresql/16/extension/
COPY initdb/ /docker-entrypoint-initdb.d/
