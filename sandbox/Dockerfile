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
    && mkdir -p /opt/opencode/bin \
    && cp /root/.opencode/bin/opencode /opt/opencode/bin/opencode \
    && ln -s /opt/opencode/bin/opencode /usr/local/bin/opencode

RUN useradd --create-home --home-dir /home/test_user --shell /bin/bash test_user \
    && mkdir -p /workspace/KLONA \
    && chown -R test_user:test_user /workspace/KLONA /home/test_user

WORKDIR /workspace/KLONA

CMD ["bash"]
