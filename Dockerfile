# syntax=docker/dockerfile:1.7
FROM python:3.11.14-slim-bookworm AS python-runtime

FROM node:22-bookworm-slim AS web-build
WORKDIR /web
ARG VITE_CESIUM_ION_TOKEN=""
ARG VITE_CESIUM_ENABLE_OSM_BUILDINGS="true"
ARG VITE_CESIUM_ENABLE_PHOTOREALISTIC="true"
ENV VITE_CESIUM_ION_TOKEN=$VITE_CESIUM_ION_TOKEN \
    VITE_CESIUM_ENABLE_OSM_BUILDINGS=$VITE_CESIUM_ENABLE_OSM_BUILDINGS \
    VITE_CESIUM_ENABLE_PHOTOREALISTIC=$VITE_CESIUM_ENABLE_PHOTOREALISTIC
RUN npm install --global pnpm@11.19.0
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm run build

FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OTTAWA_RT_CONFIG=/app/config/default.yaml
COPY --from=python-runtime /usr/local /usr/local
RUN apt-get update && apt-get install -y --no-install-recommends \
    libbz2-1.0 libffi8 liblzma5 libncursesw6 libreadline8 libsqlite3-0 libssl3 zlib1g \
    libgl1 libglib2.0-0 libgomp1 gdal-bin libgdal-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python3.11 -m pip install ".[all]" \
    && python3.11 -c "import sionna.rt; print('Sionna RT import OK')"
COPY config/ ./config/
COPY --from=web-build /web/dist ./web/dist
EXPOSE 8000
ENTRYPOINT ["ottawa-rt"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
