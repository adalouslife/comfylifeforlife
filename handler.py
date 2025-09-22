import os
import time
import json
import shutil
import base64
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

import runpod
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# -----------------------------
# Env / Paths
# -----------------------------
COMFY_HOST = os.getenv("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.getenv("COMFY_PORT", "8188"))
COMFY_URL = f"http://{COMFY_HOST}:{COMFY_PORT}"

INPUT_DIR = Path(os.getenv("INPUT_DIR", "/workspace/ComfyUI/input"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/workspace/ComfyUI/output"))
WORK_DIR = Path("/workspace")

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# -----------------------------
# Logging
# -----------------------------
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("handler")

# -----------------------------
# Robust HTTP Session
# -----------------------------
def _build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        # Some hosts close the connection if UA is missing or too generic.
        "User-Agent": "runpod-comfy-faceswap/1.0 (+https://runpod.io)",
        "Accept": "*/*",
        "Connection": "keep-alive",
    })
    retries = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.6,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_maxsize=8)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s

SESSION = _build_session()

# -----------------------------
# Utility: download or decode inputs
# -----------------------------
def _safe_filename(name: str) -> str:
    keep = "".join(c for c in name if c.isalnum() or c in ("-", "_", "."))
    return keep or "file"

def _download_with_requests(url: str, dest: Path, timeout: float = 25.0) -> None:
    with SESSION.get(url, stream=True, allow_redirects=True, timeout=timeout) as r:
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        if "text/html" in ctype and not url.lower().endswith((".jpg",".jpeg",".png",".webp",".bmp",".gif",".tiff",".tif")):
            # Servers sometimes send HTML if blocked; reject clearly.
            raise RuntimeError(f"Remote responded with HTML instead of an image (content-type={ctype}).")
        # Cap to ~100MB just in case
        max_bytes = 100 * 1024 * 1024
        got = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                got += len(chunk)
                if got > max_bytes:
                    raise RuntimeError("Downloaded file exceeds 100MB limit.")
                f.write(chunk)

def _download_with_curl(url: str, dest: Path, timeout: int = 30) -> None:
    # Fallback path when some hosts dislike Python TLS stack.
    cmd = [
        "curl", "-L", "--fail", "--show-error",
        "--max-time", str(timeout),
        "-A", "runpod-comfy-faceswap/1.0",
        "-o", str(dest),
        url,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"curl failed: {proc.stderr.strip()}")

def fetch_image_to_path(
    *,
    source_url: Optional[str] = None,
    source_b64: Optional[str] = None,
    name_hint: str = "input.jpg"
) -> Path:
    """
    Save an input image into INPUT_DIR and return the path.
    - Prefer URL if provided; else expect base64.
    - Robust retries + curl fallback for fragile hosts.
    """
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(Path(name_hint).name)
    dest = INPUT_DIR / filename

    if source_url:
        try:
            _download_with_requests(source_url, dest)
        except Exception as e_req:
            log.info(f"[fetch] Python requests failed: {e_req} — trying curl fallback.")
            try:
                _download_with_curl(source_url, dest)
            except Exception as e_curl:
                raise RuntimeError(f"Failed to download url: {source_url}; requests: {e_req}; curl: {e_curl}") from e_curl
        if dest.stat().st_size == 0:
            raise RuntimeError("Downloaded file is empty.")
        return dest

    if source_b64:
        try:
            raw = base64.b64decode(source_b64, validate=True)
        except Exception as e:
            raise RuntimeError(f"Invalid base64 image: {e}") from e
        if len(raw) == 0:
            raise RuntimeError("Base64 image is empty.")
        with open(dest, "wb") as f:
            f.write(raw)
        return dest

    raise ValueError("Provide 'source_b64' or 'source_url'.")

# -----------------------------
# ComfyUI readiness
# -----------------------------
def wait_for_comfy_ready(timeout_s: int = 60) -> None:
    import urllib.parse
    endpoint = f"{COMFY_URL}/system_stats"
    t0 = time.time()
    last_err = None
    while time.time() - t0 < timeout_s:
        try:
            r = SESSION.get(endpoint, timeout=5)
            if r.ok:
                return
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
        time.sleep(1.0)
    raise RuntimeError(f"ComfyUI did not come up in time: {last_err}")

# -----------------------------
# Minimal fake pipeline for tests
# -----------------------------
def op_health_check(_: Dict[str, Any]) -> Dict[str, Any]:
    wait_for_comfy_ready(timeout_s=30)
    return {"ok": True, "comfy_url": COMFY_URL}

# -----------------------------
# Faceswap op (skeleton – wire to your workflow submission)
# -----------------------------
def op_faceswap(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expects:
      body = {
        "op": "faceswap",
        "source_url": "...",  # or source_b64
        "target_url": "...",  # or target_b64
      }
    """
    wait_for_comfy_ready(timeout_s=60)

    src_url = body.get("source_url")
    src_b64 = body.get("source_b64")
    tgt_url = body.get("target_url")
    tgt_b64 = body.get("target_b64")

    try:
        src_path = fetch_image_to_path(source_url=src_url, source_b64=src_b64, name_hint="source.jpg")
        tgt_path = fetch_image_to_path(source_url=tgt_url, source_b64=tgt_b64, name_hint="target.jpg")
    except Exception as e:
        raise RuntimeError(f"Failed to fetch inputs: {e}") from e

    # TODO: submit your ComfyUI workflow here, using src_path and tgt_path.
    # For now, we just echo back paths to prove downloads work.
    return {
        "ok": True,
        "message": "Inputs downloaded. Wire your ComfyUI workflow call next.",
        "source_path": str(src_path),
        "target_path": str(tgt_path),
        "output_dir": str(OUTPUT_DIR),
    }

# -----------------------------
# RunPod handler
# -----------------------------
def handler(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("input") or {}
    op = (body.get("op") or body.get("operation") or "").lower().strip()

    if op in ("", "health", "health_check", "ping"):
        return op_health_check(body)
    elif op in ("faceswap", "swap", "face_swap"):
        return op_faceswap(body)
    else:
        raise RuntimeError(f"Unknown op '{op}'.")

runpod.serverless.start({"handler": handler})
