FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        tar \
        wget \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path \
    && ln -s /root/.opencode/bin/opencode /usr/local/bin/opencode

WORKDIR /workspace/KLONA

CMD ["bash"]
