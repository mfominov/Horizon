# Track Python 3.13 security patch releases on the stable Trixie base.
FROM python:3.13-slim-trixie

# Set working directory
WORKDIR /app

# Apply fixes published after the base-image layer was built.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster dependency management
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY data ./data
COPY profiles ./profiles
COPY .env.example .env.example

# Install optional extras from a comma-separated list, e.g. "trafilatura,openbb".
ARG EXTRAS=""
RUN set -eu; \
    set --; \
    if [ -n "$EXTRAS" ]; then \
        case "$EXTRAS" in ,*|*,|*,,*) echo "Invalid EXTRAS list: $EXTRAS" >&2; exit 2 ;; esac; \
        old_ifs=$IFS; IFS=,; \
        for extra in $EXTRAS; do \
            case "$extra" in [._-]*|*[._-]|*[!A-Za-z0-9._-]*) echo "Invalid extra: $extra" >&2; exit 2 ;; esac; \
            set -- "$@" --extra "$extra"; \
        done; \
        IFS=$old_ifs; \
    fi; \
    uv sync --frozen --no-dev "$@"

# Runtime data is mounted here; keep the image and process unprivileged.
RUN useradd --create-home --uid 10001 horizon \
    && chown -R horizon:horizon /app

# Create volume mount points
VOLUME ["/app/data"]

# Set environment variables
ENV PYTHONUNBUFFERED=1
USER horizon

# Run the application
ENTRYPOINT ["uv", "run", "horizon"]
CMD []
