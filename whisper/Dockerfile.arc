FROM ubuntu:24.04

# Real Ubuntu 24.04 (noble) base -- not python:3.12-slim (Debian) -- because
# Intel's GPU driver packages below are built for Ubuntu's package set, not
# Debian's; Ubuntu 24.04 already ships Python 3.12 as python3.
# ffmpeg: audio/video -> 16kHz mono wav. clinfo/vainfo: GPU-visibility verification.
# Intel's own repositories.intel.com "client" channel (not Ubuntu's archive) carries
# current Battlemage (Arc B580) compute-runtime support for Ubuntu 24.04 (noble).
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip ffmpeg clinfo vainfo wget gnupg && \
    wget -qO - https://repositories.intel.com/gpu/intel-graphics.key | \
        gpg --yes --dearmor --output /usr/share/keyrings/intel-graphics.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/intel-graphics.gpg] https://repositories.intel.com/gpu/ubuntu noble client" \
        > /etc/apt/sources.list.d/intel-gpu-noble.list && \
    apt-get update && apt-get install -y --no-install-recommends \
        intel-opencl-icd libze1 intel-level-zero-gpu \
        intel-media-va-driver-non-free libmfx-gen1 libvpl2 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-arc.txt .
# --break-system-packages: Ubuntu 24.04's apt-managed python3-pip enforces PEP 668.
RUN pip install --no-cache-dir --break-system-packages -r requirements-arc.txt
COPY server.py engine_arc.py ./

# Root, no render-group mapping: this is an isolated single-purpose service
# behind an existing reverse proxy, not handling secrets. /dev/dri's render
# node is root:render 660 by default -- root bypasses the group check.
ENV ENGINE=arc
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
