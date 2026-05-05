FROM python:3.12-slim-bookworm

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PATH="/root/.local/bin:/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Install the OpenCode CLI using the documented installer.
RUN curl -fsSL https://opencode.ai/install | sh

COPY pyproject.toml* ./
COPY src/ ./src/

RUN python -m pip install --no-cache-dir --upgrade pip \
    && if [ -f pyproject.toml ]; then python -m pip install --no-cache-dir -e .; fi

RUN mkdir -p /state
VOLUME /state
EXPOSE 8080

# The runtime is responsible for final-container OpenCode auth, model discovery,
# reasoning selection, then starting the high-level MCP server and queue worker.
CMD ["python", "-m", "memory_agent.runtime"]
