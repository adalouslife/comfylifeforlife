FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    WORKSPACE=/workspace

WORKDIR $WORKSPACE

# System deps
RUN apt-get update && apt-get install -y \
    python3 python3-pip git wget curl unzip && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    pip3 install --upgrade pip setuptools wheel

# Clone ComfyUI
RUN git clone https://github.com/comfyanonymous/ComfyUI.git

# Python deps
RUN pip install -r ComfyUI/requirements.txt runpod

# Copy our repo files
COPY . $WORKSPACE
RUN chmod +x $WORKSPACE/*.sh

EXPOSE 8000 8188
CMD ["bash", "start.sh"]

# after you clone ComfyUI into /workspace/ComfyUI
COPY comfyui/workflows/APIAutoFaceACE.json /workspace/ComfyUI/workflows/APIAutoFaceACE.json
