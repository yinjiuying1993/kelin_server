ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.22-python3.12-bookworm-slim@sha256:28df4bbd896cf66a224f2e0cb22240a9a2b9803a3a13519bcadf2e9fdd68c632

FROM ${UV_IMAGE} AS base
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_FROZEN=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --home-dir /home/app app \
    && python --version

FROM base AS deps
COPY pyproject.toml uv.lock ./

FROM deps AS toolchain
RUN uv sync --frozen --no-install-project
COPY alembic.ini ./
COPY app ./app
COPY scripts ./scripts
COPY fixtures ./fixtures
COPY tests ./tests
RUN chown -R app:app /app
USER app

FROM deps AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-install-project --no-dev
COPY alembic.ini ./
COPY app ./app
RUN chown -R app:app /app
USER app
EXPOSE 8000
CMD ["uv", "run", "--frozen", "--no-dev", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
