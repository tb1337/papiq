# syntax=docker/dockerfile:1

# Papiq image, one Dockerfile for development and operation so both use the same system packages.
#   dev      devcontainer: base + uv, Node.js, pnpm, Git
#   runtime  production image (placeholder, built in M9)
# All stages build for linux/amd64 and linux/arm64.

FROM node:24.21.0-trixie-slim AS node
FROM ghcr.io/astral-sh/uv:0.11.33 AS uv

# --- base: Python and the system packages OCRmyPDF needs ---------------------------------------
FROM python:3.13.13-slim-trixie AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Only for the build: keeps debconf from warning about a missing terminal.
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        ghostscript \
        pngquant \
        qpdf \
        tesseract-ocr \
        tesseract-ocr-deu \
        tesseract-ocr-eng \
        unpaper \
    && rm -rf /var/lib/apt/lists/*

# --- dev: devcontainer ---------------------------------------------------------------------------
FROM base AS dev

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
# host (e.g. macOS) must not clash with it, and it stays fast on Docker Desktop.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"
RUN mkdir -p /opt/venv /home/vscode/.cache/uv \
    && chown -R vscode:vscode /opt/venv /home/vscode/.cache

USER vscode
WORKDIR /workspaces/papiq

# --- runtime: production image (M9) --------------------------------------------------------------
# s6-overlay, the application and Docling follow in M9.
FROM base AS runtime
