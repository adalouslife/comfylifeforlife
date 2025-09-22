# CUDA 12.1 + Ubuntu base (as you used)
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKDIR=/workspace

WORKDIR ${WORKDIR}

# OS deps + Python
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip \
        git curl ca-certificates wget tini ffmpeg \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

# ComfyUI
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git ${WORKDIR}/ComfyUI

# Copy repo files
COPY requirements.txt ${WORKDIR}/requirements.txt
COPY install_custom_nodes.py ${WORKDIR}/install_custom_nodes.py
COPY custom_nodes.txt ${WORKDIR}/custom_nodes.txt
COPY download_models.sh ${WORKDIR}/download_models.sh
COPY handler.py ${WORKDIR}/handler.py
COPY start.sh ${WORKDIR}/start.sh
COPY comfyui ${WORKDIR}/comfyui

# Python deps
RUN python3 -m pip install --upgrade pip \
    && python3 -m pip install -r ${WORKDIR}/requirements.txt

# Custom nodes + models (best-effort)
RUN python3 ${WORKDIR}/install_custom_nodes.py || true
RUN bash ${WORKDIR}/download_models.sh || true

# Comfy ports are local to container; RunPod hits the handler (8000)
EXPOSE 8000
EXPOSE 8188

# Use tini + python handler
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-u", "handler.py"]
