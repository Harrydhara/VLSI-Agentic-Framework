# syntax=docker/dockerfile:1
# The VLSI design agent with its EDA tools and dashboard. The language model is not in this
# image: it runs in Ollama (another container or the host), reached at VLSI_AGENT_OLLAMA_URL.
#
#   docker build -t vlsi-agent .
#   docker run --rm -p 7860:7860 -e VLSI_AGENT_OLLAMA_URL=http://<ollama-host>:11434 vlsi-agent
#   docker run --rm vlsi-agent vlsi-agent check-fixtures      # any CLI command works too

ARG UBUNTU=ubuntu:24.04

# ---------------------------------------------------------------------------- Yosys
# Built from the same commit as the machine the benchmarks ran on (Yosys 0.57+260):
# synthesis cell counts can change between versions, and Ubuntu's package is 0.33.
FROM ${UBUNTU} AS yosys
ARG YOSYS_COMMIT=ed4eb6d33
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential bison flex libfl-dev gawk git ca-certificates pkg-config python3 \
        libreadline-dev tcl-dev libffi-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --quiet https://github.com/YosysHQ/yosys.git /src/yosys \
    && cd /src/yosys \
    && git checkout --quiet "${YOSYS_COMMIT}" \
    && git submodule update --init --recursive \
    && make config-gcc \
    && make -j"$(nproc)" PREFIX=/usr/local \
    && make install PREFIX=/usr/local DESTDIR=/stage \
    && /stage/usr/local/bin/yosys -V

# ---------------------------------------------------------------------------- app
FROM ${UBUNTU}
# Icarus Verilog 12 from Ubuntu 24.04, as on the benchmark machine; runtime libraries for Yosys.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        iverilog python3 python3-venv libreadline8t64 libtcl8.6 libffi8 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=yosys /stage/usr/local/ /usr/local/

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GRADIO_ANALYTICS_ENABLED=False
WORKDIR /app
# Editable install: the package finds config/, circuits/ and prompts/ next to src/.
# Exact dependency versions come from docker/constraints.txt (what the project was tested with).
COPY pyproject.toml README.md ./
COPY docker/constraints.txt docker/
COPY src src
RUN python3 -m venv /opt/venv \
    && pip install --no-cache-dir -c docker/constraints.txt -e ".[ui,dev]"
COPY config config
COPY circuits circuits
COPY prompts prompts
COPY examples examples
COPY tests tests

# Run as an ordinary user. UID 1000 is the usual first user on Linux and WSL, so files the
# container writes to a mounted runs/ folder belong to you. (Ubuntu's image ships a user
# "ubuntu" with that UID; it is replaced by "agent".)
RUN userdel --remove ubuntu \
    && useradd --create-home --uid 1000 agent \
    && mkdir -p runs prompts/custom \
    && chown agent:agent runs prompts/custom
USER agent

EXPOSE 7860
CMD ["vlsi-agent", "ui", "--host", "0.0.0.0", "--port", "7860"]
