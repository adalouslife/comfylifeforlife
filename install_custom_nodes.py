#!/usr/bin/env python3
import os, subprocess, sys, time, tempfile, shutil, pathlib

CUSTOM_DIR = "/workspace/ComfyUI/custom_nodes"
CONSTRAINTS = "/workspace/constraints.txt"

REPOS = [
    # Manager first (some nodes expect it)
    "https://github.com/ltdrdata/ComfyUI-Manager.git",
    # Your required set
    "https://github.com/ltdrdata/ComfyUI-Impact-Pack.git",
    "https://github.com/Derfuu/ComfyUI-Inpaint-CropAndStitch.git",
    "https://github.com/kijai/ComfyUI-KJNodes.git",
    "https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git",
    "https://github.com/cubiq/ComfyUI_essentials.git",
    "https://github.com/Acly/comfyui_face_parsing.git",
    "https://github.com/rgthree/rgthree-comfy.git",
]

# Lines in requirements.txt that we do NOT want to install as-is
# because they tend to force gigantic CUDA upgrades or break torch 2.1.1.
BLOCK_PATTERNS = (
    "torch", "torchvision", "torchaudio",
    "xformers", "triton", "flash-attn",
    "git+https://github.com/facebookresearch/sam2",
)

def run(cmd, cwd=None, check=True):
    print(f"[run] {' '.join(cmd)}  (cwd={cwd})", flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check)

def clone_with_retry(url, dest, tries=3):
    for i in range(1, tries+1):
        try:
            run(["git", "clone", "--depth=1", url, dest])
            return True
        except subprocess.CalledProcessError as e:
            print(f"[warn] clone failed ({i}/{tries}) for {url}: {e}", flush=True)
            time.sleep(3 * i)
    print(f"[error] clone failed for {url}; skipping", flush=True)
    return False

def filtered_requirements_path(req_path: str) -> str:
    """Write a filtered copy of requirements that removes BLOCK_PATTERNS lines."""
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="req_", suffix=".txt")
    os.close(tmp_fd)
    removed = []
    with open(req_path, "r", encoding="utf-8", errors="ignore") as fin, \
         open(tmp_path, "w", encoding="utf-8") as fout:
        for line in fin:
            lower = line.strip().lower()
            if any(b in lower for b in BLOCK_PATTERNS):
                removed.append(line.strip())
                continue
            fout.write(line)
    if removed:
        print(f"[info] filtered out from {req_path}:\n  - " + "\n  - ".join(removed), flush=True)
    return tmp_path

def pip_install_requirements(req_file: str):
    # Prefer constraints; if not present, just install normally.
    if os.path.exists(CONSTRAINTS):
        print(f"[info] installing with constraints: {CONSTRAINTS}", flush=True)
        run([sys.executable, "-m", "pip", "install", "-r", req_file, "--constraint", CONSTRAINTS])
    else:
        run([sys.executable, "-m", "pip", "install", "-r", req_file])

def main():
    os.makedirs(CUSTOM_DIR, exist_ok=True)
    print(f"[info] custom nodes dir = {CUSTOM_DIR}")

    for url in REPOS:
        name = url.split("/")[-1].removesuffix(".git")
        dest = os.path.join(CUSTOM_DIR, name)
        if os.path.exists(dest):
            print(f"[skip] already present: {name}")
            continue

        print(f"[info] cloning {url} -> {dest}")
        if not clone_with_retry(url, dest):
            continue

        req_txt = os.path.join(dest, "requirements.txt")
        if os.path.isfile(req_txt):
            print(f"[info] found requirements.txt in {name} -> installing")
            try:
                filt = filtered_requirements_path(req_txt)
                try:
                    pip_install_requirements(filt)
                finally:
                    try:
                        os.remove(filt)
                    except Exception:
                        pass
            except subprocess.CalledProcessError as e:
                print(f"[error] pip install failed for {name}; continuing: {e}", flush=True)

    # Safety: re-pin transformers to match torch 2.1.1
    try:
        run([sys.executable, "-m", "pip", "install", "transformers<4.45"])
    except subprocess.CalledProcessError:
        print("[warn] could not re-pin transformers; continuing", flush=True)

    print("[done] custom nodes installation completed", flush=True)

if __name__ == "__main__":
    main()
