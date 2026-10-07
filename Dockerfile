# syntax=docker/dockerfile:1

# Papiq image, one Dockerfile for development and operation so both use the same system packages.
#   docling-models  Docling's layout and table models, downloaded at build time
#   dev             devcontainer: base + uv, Node.js, pnpm, Git, Docling models
#   runtime         production image (placeholder, built in M9)
# All stages build for linux/amd64 and linux/arm64; docling-models runs on the build machine.

FROM node:24.21.0-trixie-slim AS node
FROM ghcr.io/astral-sh/uv:0.11.33 AS uv
FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:0.11.33 AS uv-build

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

# --- docling-models: the models Docling needs, never downloaded at run time ---------------------
# Installs the locked dependencies (Docling, PyTorch CPU) in a throwaway environment and downloads
# the models of exactly that Docling version. Rebuilt only when the lock file changes. The model
# files are the same on every platform, so this stage runs natively on the build machine (no
# emulation for arm64) and the target images copy the result.
FROM --platform=$BUILDPLATFORM python:3.13.13-slim-trixie AS docling-models

# Only for the build: keeps debconf from warning about a missing terminal.
ARG DEBIAN_FRONTEND=noninteractive
# OpenCV, which Docling loads.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libgl1 libglib2.0-0t64 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv-build /uv /usr/local/bin/
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
WORKDIR /build
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev \
    && .venv/bin/docling-tools models download layout tableformer -o /opt/docling-models \
    && rm -rf /build /root/.cache

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
WORKDIR /workspaces/papiq

# --- runtime: production image (M9) --------------------------------------------------------------
# s6-overlay, the application and Docling follow in M9.
FROM base AS runtime
