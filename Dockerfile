# --- Stage 1: Build wheel ---------------------------------------------------
FROM python:3.13-slim AS builder

WORKDIR /build

# Install build tooling (hatchling)
RUN pip install --no-cache-dir hatchling build

# Copy only what hatchling needs to build the wheel
COPY pyproject.toml README.md LICENSE ./
COPY cohort/ cohort/

RUN python -m build --wheel --outdir /build/dist

# --- Stage 2: Runtime --------------------------------------------------------
FROM python:3.13-slim AS runtime

LABEL maintainer="Ryan Wheeler"
LABEL description="cohort -- Multi-agent orchestration with loop prevention and contribution scoring"
LABEL license="MIT"
LABEL org.opencontainers.image.source="https://github.com/rwheeler007/cohort"

# Build arg: set to "claude" to install cohort[claude] extras
ARG INSTALL_EXTRAS=""

# Non-root user
RUN groupadd --gid 1000 cohort \
    && useradd --uid 1000 --gid cohort --create-home cohort

WORKDIR /home/cohort

# Copy the built wheel from the builder stage
COPY --from=builder /build/dist/*.whl /tmp/

# Install the wheel (with optional extras)
RUN if [ -n "$INSTALL_EXTRAS" ]; then \
        pip install --no-cache-dir "/tmp/cohort-*.whl[$INSTALL_EXTRAS]"; \
    else \
        pip install --no-cache-dir /tmp/cohort-*.whl; \
    fi \
    && rm -f /tmp/*.whl

USER cohort

# See .dockerignore for files excluded from the build context
ENTRYPOINT ["python", "-m", "cohort"]
