# Stage 1: Build
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev 2>/dev/null || \
    uv sync --no-install-project --no-dev

COPY src/ ./src/

# Stage 2: Runtime
FROM python:3.12-slim-bookworm

RUN groupadd --gid 1000 mcp && \
    useradd --uid 1000 --gid mcp --shell /bin/false mcp

WORKDIR /app
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

RUN mkdir -p /vault && chown mcp:mcp /vault
VOLUME /vault

USER mcp
EXPOSE 8000

CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]
