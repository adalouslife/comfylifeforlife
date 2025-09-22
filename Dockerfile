# Base image (CUDA 12.1 + Python)
FROM runpod/base:0.6.2-cuda12.1.1

# Prevent interactive tzdata etc.
ENV DEBIAN_FRONTEND=noninteractive

# System deps: curl for robust URL fallback
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Create workspace
WORKDIR /workspace

# Copy project files
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /workspace

# Ensure scripts are executable
RUN chmod +x /workspace/start.sh || true

# Expose ComfyUI port (internal)
ENV COMFY_HOST=127.0.0.1
ENV COMFY_PORT=8188

# Default input/output (can be overridden by env)
ENV INPUT_DIR=/workspace/ComfyUI/input
ENV OUTPUT_DIR=/workspace/ComfyUI/output

# RunPod handler port
ENV RP_HANDLER_PORT=8000

# Start the ComfyUI backend + handler
CMD ["/bin/bash", "-lc", "/workspace/start.sh"]
