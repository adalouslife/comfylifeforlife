# ========= Base image =========
FROM runpod/pytorch:3.10-2.3.1-12.1.1

# Keep the workspace tidy
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

# ---------- System deps ----------
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git wget curl ca-certificates ffmpeg libgl1 \
 && rm -rf /var/lib/apt/lists/*

# ---------- Copy repo ----------
# (we keep the same layout you already have)
COPY . /workspace

# ---------- Python deps ----------
# ComfyUI deps + runpod handler + anything your requirements specify
RUN pip install --upgrade pip \
 && pip install -r /workspace/requirements.txt \
 && pip install runpod

# ---------- Get ComfyUI ----------
RUN git clone --depth=1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI \
 && pip install -r /workspace/ComfyUI/requirements.txt

# ---------- Install custom nodes (from your repo scripts) ----------
# This clones the packs listed in custom_nodes.txt into ComfyUI/custom_nodes
RUN python3 /workspace/install_custom_nodes.py

# ---------- Download model files (from your repo script) ----------
# This should populate /workspace/ComfyUI/models/* with what your workflow needs
RUN bash /workspace/download_models.sh

# Pre-create IO dirs so handler can drop inputs/outputs
RUN mkdir -p /workspace/inputs /workspace/outputs

# ---------- Ports ----------
# ComfyUI UI/API
EXPOSE 8188
# RunPod handler port (internal)
EXPOSE 8000

# ---------- Start both ----------
# start.sh launches ComfyUI in background, then keeps handler in foreground (required by RunPod)
ENTRYPOINT ["/bin/bash", "/workspace/start.sh"]
