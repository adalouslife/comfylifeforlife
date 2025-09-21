import os, time, json, tempfile, subprocess, signal
from pathlib import Path
from typing import Dict, Any, Optional
import requests
import runpod  # RunPod Serverless SDK

COMFY_HOST = os.getenv("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.getenv("COMFY_PORT", "8188"))
COMFY_URL  = f"http://{COMFY_HOST}:{COMFY_PORT}"
WORKDIR    = Path(os.getenv("WORKDIR", "/workspace"))
STORAGE    = Path(os.getenv("STORAGE_DIR", "/runpod-volume")).expanduser()

# Optional: default workflow path (can be replaced per job)
DEFAULT_WORKFLOW = WORKDIR / "comfyui" / "workflows" / "APIAutoFaceACE.json"

comfy_proc: Optional[subprocess.Popen] = None


def _start_comfy():
    """Start ComfyUI as a background process."""
    global comfy_proc
    if comfy_proc and comfy_proc.poll() is None:
        return

    env = os.environ.copy()
    comfy_cmd = [
        "python3", "main.py",
        "--listen", COMFY_HOST,
        "--port", str(COMFY_PORT),
        "--enable-cors-header"
    ]
    comfy_cwd = str(WORKDIR / "ComfyUI")

    # Log to files to avoid blocking stdout
    log_dir = WORKDIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_f = open(log_dir / "comfy_stdout.log", "a", buffering=1)
    stderr_f = open(log_dir / "comfy_stderr.log", "a", buffering=1)

    comfy_proc = subprocess.Popen(
        comfy_cmd,
        cwd=comfy_cwd,
        env=env,
        stdout=stdout_f,
        stderr=stderr_f,
        start_new_session=True
    )


def _wait_for_comfy(timeout: int = 180) -> bool:
    """Wait until ComfyUI HTTP responds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(COMFY_URL, timeout=3)
            if r.status_code in (200, 404):  # 200 for index, 404 for missing route
                return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


def _download_to_tmp(url: str, suffix: str) -> Path:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    fd, p = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(r.content)
    return Path(p)


def _post_prompt(payload: Dict[str, Any]) -> str:
    r = requests.post(f"{COMFY_URL}/prompt", json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    return data["prompt_id"]


def _get_images(prompt_id: str) -> Dict[str, Any]:
    # Poll history for outputs
    for _ in range(240):  # ~4 minutes
        resp = requests.get(f"{COMFY_URL}/history/{prompt_id}", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data and prompt_id in data and data[prompt_id].get("outputs"):
                return data[prompt_id]["outputs"]
        time.sleep(1.0)
    raise RuntimeError("Timed out waiting for ComfyUI output.")


def _upload_catbox(bytes_data: bytes, filename: str = "output.png") -> str:
    # Simple, anonymous upload to catbox (test convenience only)
    files = {'fileToUpload': (filename, bytes_data)}
    data = {'reqtype': 'fileupload'}
    r = requests.post("https://catbox.moe/user/api.php", data=data, files=files, timeout=60)
    r.raise_for_status()
    return r.text.strip()


def _ensure_ready():
    _start_comfy()
    if not _wait_for_comfy():
        raise RuntimeError("ComfyUI did not become ready.")


# ----------------------------
# RunPod job handler
# ----------------------------
def handler(job):
    """
    Supported ops:
      - op: "health" | "health_check"  -> returns { ok: true }
      - op: "comfy_passthrough"        -> provide a raw ComfyUI payload in 'workflow'
      - op: "swap" (optional stub)     -> expects 'source_url' and 'target_url'
    """
    inp: Dict[str, Any] = job.get("input") or {}

    op = (inp.get("op") or "health").lower()
    if op in ("health", "health_check", "ping"):
        return {"ok": True, "comfy_url": COMFY_URL}

    # Make sure ComfyUI is up
    _ensure_ready()

    if op == "comfy_passthrough":
        payload = inp.get("workflow")
        if not payload:
            return {"error": "Provide 'workflow' (ComfyUI prompt payload)."}
        prompt_id = _post_prompt(payload)
        outputs = _get_images(prompt_id)

        # Try to retrieve first image, upload, and return URL
        for _, node_out in outputs.items():
            images = node_out.get("images", [])
            if images:
                # Fetch the first produced image via the ComfyUI API
                first = images[0]
                fn = first.get("filename")
                sub = first.get("subfolder", "")
                typ = first.get("type", "output")
                img = requests.get(f"{COMFY_URL}/view?filename={fn}&subfolder={sub}&type={typ}", timeout=60)
                img.raise_for_status()
                url = _upload_catbox(img.content, filename=fn or "result.png")
                return {"ok": True, "url": url, "prompt_id": prompt_id}
        return {"error": "No image outputs found.", "prompt_id": prompt_id}

    if op == "swap":
        # Stub: you can wire your face-swap workflow here if needed.
        # For tests, we do not perform heavy operations.
        return {"ok": True, "note": "swap stub; wire your workflow in handler.py"}

    return {"error": f"Unknown op '{op}'."}


# Start the queue worker with concurrency 1 (safer for Comfy)
runpod.serverless.start(
    {
        "handler": handler,
        "concurrency": 1
    }
)

# Graceful stop to clean Comfy on container shutdown
def _cleanup(*_):
    global comfy_proc
    if comfy_proc and comfy_proc.poll() is None:
        try:
            os.killpg(comfy_proc.pid, signal.SIGTERM)
        except Exception:
            pass
signal.signal(signal.SIGTERM, _cleanup)
signal.signal(signal.SIGINT, _cleanup)
