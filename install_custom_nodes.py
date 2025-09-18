#!/usr/bin/env python3
"""
install_custom_nodes.py
Idempotent installer for ComfyUI custom nodes.

Usage: python3 /workspace/install_custom_nodes.py

Reads /workspace/custom_nodes.txt (one git url per line, supports comments starting with #)
Clones each repo into /workspace/ComfyUI/custom_nodes/<repo_name>
If already present, does a `git -C <dir> pull --ff-only` (best-effort).
If node repo contains requirements.txt at its root, pip-installs it (best-effort).
Logs actions to stdout for RunPod logs.
"""

import os
import subprocess
import pathlib
import sys
from urllib.parse import urlparse

WORKSPACE = os.environ.get("WORKSPACE", "/workspace")
COMFY_DIR = os.path.join(WORKSPACE, "ComfyUI")
CUSTOM_DIR = os.path.join(COMFY_DIR, "custom_nodes")
NODES_LIST = os.path.join(WORKSPACE, "custom_nodes.txt")

def safe_run(cmd, cwd=None, check=True):
    print(f"[run] {' '.join(cmd)}  (cwd={cwd})")
    try:
        subprocess.check_call(cmd, cwd=cwd)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[error] command failed: {' '.join(cmd)} -> {e}")
        return False
    except FileNotFoundError as e:
        print(f"[error] binary not found: {cmd[0]} -> {e}")
        return False

def git_repo_name(url):
    # derive a folder name from the URL
    # handles urls like https://github.com/user/repo.git or git@github:...
    path = urlparse(url).path
    name = os.path.basename(path)
    if name.endswith(".git"):
        name = name[:-4]
    return name or "repo"

def ensure_dirs():
    pathlib.Path(CUSTOM_DIR).mkdir(parents=True, exist_ok=True)
    print(f"[info] custom nodes dir = {CUSTOM_DIR}")

def read_list():
    if not os.path.exists(NODES_LIST):
        print(f"[warn] {NODES_LIST} not found — nothing to do")
        return []
    with open(NODES_LIST, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
    return lines

def clone_or_update(url):
    name = git_repo_name(url)
    dest = os.path.join(CUSTOM_DIR, name)
    if os.path.exists(dest):
        print(f"[info] exists -> trying git pull: {name}")
        # best-effort pull
        ok = safe_run(["git", "-C", dest, "pull", "--ff-only"])
        if not ok:
            print(f"[warn] git pull failed for {name}; leaving existing copy")
    else:
        print(f"[info] cloning {url} -> {dest}")
        ok = safe_run(["git", "clone", "--depth=1", url, dest])
        if not ok:
            print(f"[error] clone failed for {url}; skipping")
            return
    # try to install requirements if present
    req = os.path.join(dest, "requirements.txt")
    if os.path.exists(req):
        print(f"[info] found requirements.txt in {name} -> installing")
        # use pip from the current python
        ok = safe_run([sys.executable, "-m", "pip", "install", "-r", req])
        if not ok:
            print(f"[warn] pip install failed for {name}; continuing")
    else:
        print(f"[info] no requirements.txt for {name}")

def main():
    ensure_dirs()
    urls = read_list()
    if not urls:
        print("[info] no custom nodes to install (custom_nodes.txt empty or missing)")
        return
    for url in urls:
        try:
            clone_or_update(url)
        except Exception as e:
            print(f"[error] unexpected failure for {url}: {e}")
    print("[done] custom nodes installation completed")

if __name__ == "__main__":
    main()
