# syntax=docker/dockerfile:1

# Papiq image, one Dockerfile for development and operation so both use the same system packages.
#   base            Python and the system packages OCRmyPDF and Docling need
#   deps            the locked dependencies (Docling, PyTorch CPU) in /opt/papiq/venv, no dev group
#   docling-models  Docling's layout and table models, downloaded at build time (from deps)
#   app             deps + the Papiq package
#   s6              s6-overlay, unpacked (version and checksums pinned below)
#   web             the web UI (web/), built once on the build machine: static files only
#   dev             devcontainer: base + uv, Node.js, pnpm, Git, Docling models
#   runtime         the production image: base + s6-overlay + app + models + web UI +
#                   deploy/image/rootfs (no Node.js)
# All stages build for linux/amd64 and linux/arm64, each natively on its own machine (CI uses an
# arm64 runner; building the other architecture locally runs under emulation and is slow).
#
#   docker build --target runtime -t papiq:local .
#
# The runtime image runs s6-overlay as PID 1 (see deploy/README.md): `init-papiq` and
# `init-migrations` once, then `svc-api` and `svc-worker` as PAPIQ_ROLE says. The Papiq
# processes run as PUID:PGID (default 1000:1000), never as root. No secret is part of the
# image or of its build arguments: configuration is read from the environment at run time.

FROM node:24.21.0-trixie-slim AS node
FROM ghcr.io/astral-sh/uv:0.11.33 AS uv

# Set by BuildKit; declared here so that `FROM s6-${TARGETARCH}` can use it.
ARG TARGETARCH

# --- base: Python and the system packages OCRmyPDF and Docling need -----------------------------
# libgl1 and libglib2.0-0t64: OpenCV, which Docling loads.
FROM python:3.13.13-slim-trixie AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Only for the build: keeps debconf from warning about a missing terminal.
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        ghostscript \
        libgl1 \
        libglib2.0-0t64 \
        pngquant \
        qpdf \
        tesseract-ocr \
        tesseract-ocr-deu \
        tesseract-ocr-eng \
        unpaper \
    && rm -rf /var/lib/apt/lists/*

# --- deps: the locked dependencies, without the dev group and without Papiq itself --------------
# One layer for PyTorch and Docling that only a change of the lock file rebuilds; `docling-models`
# and `app` build on it. The environment's Python is the image's own (same path in `runtime`).
FROM base AS deps

COPY --from=uv /uv /usr/local/bin/
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/papiq/venv
WORKDIR /build
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# --- docling-models: the models Docling needs, never downloaded at run time ---------------------
# Downloads the models of exactly the locked Docling version. The target images copy the result.
FROM deps AS docling-models

# Optional build secret `hf_token` (a Hugging Face token) avoids Hugging Face's rate limit; it
# does not end up in the image.
RUN --mount=type=secret,id=hf_token,env=HF_TOKEN \
    /opt/papiq/venv/bin/docling-tools models download layout tableformer -o /opt/docling-models

# --- app: Papiq itself, installed (not editable) into the environment ---------------------------
FROM deps AS app

COPY backend/README.md ./
COPY backend/src ./src
RUN uv sync --frozen --no-dev --no-editable

# --- s6-overlay: process supervision, PID 1 of the runtime image --------------------------------
# Pinned version; each archive is checked against its SHA-256 (from the release's .sha256 files).
FROM scratch AS s6-noarch
ADD --checksum=sha256:5379750ed30a84bbd2e2dd74847ba6b5bd29cd0b2e3ea2ec58049b57eb2eda12 \
    https://github.com/just-containers/s6-overlay/releases/download/v3.2.3.2/s6-overlay-noarch.tar.xz \
    /s6-overlay-noarch.tar.xz

FROM scratch AS s6-amd64
ADD --checksum=sha256:e6befcc96a437a3831386ecfc51808c5d3e939dc5fe3c02ae9284599e8aa2408 \
    https://github.com/just-containers/s6-overlay/releases/download/v3.2.3.2/s6-overlay-x86_64.tar.xz \
    /s6-overlay-arch.tar.xz

FROM scratch AS s6-arm64
ADD --checksum=sha256:b17f17a82e7a515c682a91edaf2ffdabb73f891981b6c1fd712115693a2f8b4c \
    https://github.com/just-containers/s6-overlay/releases/download/v3.2.3.2/s6-overlay-aarch64.tar.xz \
    /s6-overlay-arch.tar.xz

FROM s6-${TARGETARCH} AS s6-arch

FROM base AS s6
# Only to unpack the archives; xz-utils does not end up in the image.
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install --no-install-recommends -y xz-utils \
    && rm -rf /var/lib/apt/lists/*
COPY --from=s6-noarch / /archives/
COPY --from=s6-arch / /archives/
RUN mkdir /s6 && for archive in /archives/*.tar.xz; do tar -C /s6 -Jxpf "$archive"; done

# --- web: the web UI, built on the build machine's platform -----------------------------------
# The result is static files, the same for every target platform: no emulation for arm64.
# Dependencies first, so that a change of the code alone keeps the install layer.
FROM --platform=$BUILDPLATFORM node:24.21.0-trixie-slim AS web
RUN npm install --global pnpm@12.9.1
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# --- dev: devcontainer ---------------------------------------------------------------------------
FROM base AS dev

# Docling models (PAPIQ_DOCLING_MODELS_PATH defaults to this directory).
COPY --from=docling-models /opt/docling-models /opt/docling-models

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        curl \
        git \
        openssh-client \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Non-root user; passwordless sudo is needed by the devcontainer docker-outside-of-docker feature.
RUN useradd --create-home --uid 1000 --shell /bin/bash vscode \
    && echo "vscode ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/vscode \
    && chmod 0440 /etc/sudoers.d/vscode

COPY --from=uv /uv /uvx /usr/local/bin/

# Node.js LTS from the official image (same Debian release, both architectures).
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install --global pnpm@12.9.1

# The virtual environment lives outside the bind-mounted workspace: a `.venv` created on the
# host (e.g. macOS) must not clash with it, and it stays fast on Docker Desktop. It sits in the
# home directory so that it follows the user if the devcontainer remaps the UID.
ENV UV_PROJECT_ENVIRONMENT=/home/vscode/.venv \
    UV_LINK_MODE=copy \
    PATH="/home/vscode/.venv/bin:$PATH"
RUN mkdir -p /home/vscode/.venv /home/vscode/.cache/uv \
    && chown -R vscode:vscode /home/vscode/.venv /home/vscode/.cache

USER vscode

# Mount targets for the persistent auth volumes (see "volumes" in
# .devcontainer/compose.yml). Created here as vscode so a fresh named volume inherits
# vscode ownership; otherwise Docker creates them root-owned and Claude Code /
# gh cannot write their credentials.
RUN mkdir -p /home/vscode/.claude /home/vscode/.config/gh

# Claude Code CLI (native installer -> ~/.local/bin/claude). The VS Code
# extension and the CLI share the login stored in the claude-config volume.
RUN curl -fsSL https://claude.ai/install.sh | bash

ENV CLAUDE_CONFIG_DIR=/home/vscode/.claude

WORKDIR /workspaces/papiq

# --- runtime: production image ------------------------------------------------------------------
FROM base AS runtime

COPY --from=s6 /s6/ /
COPY --from=app /opt/papiq/venv /opt/papiq/venv
COPY --from=docling-models /opt/docling-models /opt/docling-models
COPY --from=web /web/build /opt/papiq/ui
# The s6 services and scripts.
COPY deploy/image/rootfs/ /

# /command holds the s6 programs (`docker exec` shells need it in PATH).
# s6-overlay: a failing init service stops the container (exit code 1, reason in the log).
# Shutdown on `docker stop`: the worker gets PAPIQ_WORKER_SHUTDOWN_TIMEOUT (30 s) to finish
# its jobs, so s6 waits 40 s for the services; Compose `stop_grace_period` is 60 s. s6 sits out the
# whole S6_KILL_GRACETIME before it ends, so that stays short (1 s). Raise the first two together.
# The database and the objects live on the volume /data (the defaults of settings.py are
# relative paths). The API serves the web UI below /ui. PUID and PGID are not Papiq settings:
# the user of the Papiq processes.
ENV PATH="/command:/opt/papiq/venv/bin:$PATH" \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2 \
    S6_SERVICES_GRACETIME=40000 \
    S6_KILL_GRACETIME=1000 \
    PAPIQ_DB_SQLITE_PATH=/data/papiq.db \
    PAPIQ_STORAGE_PATH=/data/objects \
    PAPIQ_UI_DIR=/opt/papiq/ui \
    PUID=1000 \
    PGID=1000

LABEL org.opencontainers.image.title="Papiq" \
      org.opencontainers.image.description="Self-hosted, headless document management system" \
      org.opencontainers.image.source="https://github.com/tb1337/papiq" \
      org.opencontainers.image.licenses="GPL-3.0-only"

VOLUME /data
EXPOSE 8000
HEALTHCHECK --start-period=120s --interval=30s --timeout=10s --retries=3 \
    CMD ["/usr/local/bin/papiq-healthcheck"]
ENTRYPOINT ["/init"]
