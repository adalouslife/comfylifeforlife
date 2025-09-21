import os
import json
import time
import uuid
import httpx
import requests
from pathlib import Path
import runpod

# -------------------------
# Environment / constants
# -------------------------
COMFY_HOST = os.getenv("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.getenv("COMFY_PORT", "8188"))
COMFY_BASE = f"http://{COMFY_HOST}:{COMFY_PORT}"

WORKFLOW_PATH = os.getenv("WORKFLOW_PATH", "/workspace/comfyui/workflows/APIAutoFaceACE.json")
INPUT_DIR = Path(os.getenv("INPUT_DIR", "/workspace/inputs"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/workspace/outputs"))
UPLOAD_PROVIDER = os.getenv("UPLOAD_PROVIDER", "catbox")  # catbox | none

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HTTP_TIMEOUT = 60
POLL_TIMEOUT = 300
POLL_INTERVAL = 1.0

# -------------------------
# Helpers
# -------------------------
def wait_for_comfyui(timeout=POLL_TIMEOUT):
    """Wait until ComfyUI HTTP API is reachable."""
    deadline = time.time() + timeout
    url = f"{COMFY_BASE}/queue/status"
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=5)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def download_to(path: Path, url: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=HTTP_TIMEOUT) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
    return path


def load_workflow(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def guess_and_inject_images(prompt: dict, source_path: str, target_path: str) -> dict:
    """
    Try to inject the downloaded file paths into the first two image-accepting nodes.
    This is generic and works for most workflows that use 'LoadImage' or
    accept 'image'/'filename'/'path' style inputs.
    """
    set_count = 0
    for node_id, node in prompt.items():
        inputs = node.get("inputs", {})
        # Keys that often represent local image paths in ComfyUI nodes:
        for key in ["image", "filename", "path", "file", "input_image"]:
            if key in inputs and isinstance(inputs[key], str):
                if set_count == 0:
                    inputs[key] = source_path
                    set_count += 1
                elif set_count == 1:
                    inputs[key] = target_path
                    set_count += 1
                    return prompt
        # Some nodes hide filenames under dicts
        for key, val in list(inputs.items()):
            if isinstance(val, dict):
                for k2 in ["image", "filename", "path", "file", "input_image"]:
                    if k2 in val and isinstance(val[k2], str):
                        if set_count == 0:
                            val[k2] = source_path
                            set_count += 1
                        elif set_count == 1:
                            val[k2] = target_path
                            set_count += 1
                            return prompt
    return prompt


def post_prompt(prompt: dict) -> str:
    url = f"{COMFY_BASE}/prompt"
    r = httpx.post(url, json={"prompt": prompt}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data.get("prompt_id")


def fetch_history(prompt_id: str) -> dict | None:
    url = f"{COMFY_BASE}/history/{prompt_id}"
    try:
        r = httpx.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def wait_for_images(prompt_id: str, timeout=POLL_TIMEOUT) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        h = fetch_history(prompt_id)
        # Expected structure: { prompt_id: { "outputs": { node_id: { "images": [{filename, subfolder, type}, ...] } } } }
        if h and prompt_id in h:
            outputs = h[prompt_id].get("outputs", {})
            images = []
            for _nid, out in outputs.items():
                for img in out.get("images", []):
                    images.append(img)
            if images:
                return images
        time.sleep(POLL_INTERVAL)
    raise TimeoutError("Timed out waiting for ComfyUI images.")


def pull_image_bytes_from_comfy(img_meta: dict) -> bytes:
    # img_meta example: {"filename":"something.png","subfolder":"","type":"output"}
    params = {
        "filename": img_meta["filename"],
        "subfolder": img_meta.get("subfolder", ""),
        "type": img_meta.get("type", "output"),
    }
    url = f"{COMFY_BASE}/view"
    r = httpx.get(url, params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.content


def save_bytes_to_output(content: bytes, suffix=".png") -> Path:
    out_path = OUTPUT_DIR / f"{uuid.uuid4().hex}{suffix}"
    with open(out_path, "wb") as f:
        f.write(content)
    return out_path


def upload_to_catbox(file_path: Path) -> str:
    """
    Anonymous upload to catbox.moe (simple and reliable).
    Returns a public URL.
    """
    with open(file_path, "rb") as f:
        resp = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (file_path.name, f)},
            timeout=HTTP_TIMEOUT,
        )
    resp.raise_for_status()
    url = resp.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise RuntimeError(f"Unexpected catbox response: {url}")
    return url


# -------------------------
# RunPod Job Handler
# -------------------------
def job_handler(event):
    """
    event format:
    {
      "input": {
        "op": "health_check" | "faceswap",
        "source_url": "https://...",
        "target_url": "https://...",
        "workflow_path": "/workspace/comfyui/workflows/APIAutoFaceACE.json" (optional)
      }
    }
    """
    inp = (event or {}).get("input") or {}
    op = (inp.get("op") or "health_check").lower()

    # Ensure ComfyUI is reachable for any op
    if not wait_for_comfyui():
        return {"ok": False, "error": "ComfyUI API not reachable on 127.0.0.1:8188"}

    if op == "health_check":
        # Light ping using /queue/status
        r = httpx.get(f"{COMFY_BASE}/queue/status", timeout=HTTP_TIMEOUT)
        return {"ok": True, "comfyui": r.json() if r.status_code == 200 else {"status": r.status_code}}

    if op == "faceswap":
        source_url = inp.get("source_url")
        target_url = inp.get("target_url")
        if not source_url or not target_url:
            return {"ok": False, "error": "Provide 'source_url' and 'target_url'."}

        sid = uuid.uuid4().hex[:8]
        spath = str(download_to(INPUT_DIR / f"source_{sid}.img", source_url))
        tpath = str(download_to(INPUT_DIR / f"target_{sid}.img", target_url))

        wf_path = inp.get("workflow_path", WORKFLOW_PATH)
        prompt = load_workflow(wf_path)
        prompt = guess_and_inject_images(prompt, spath, tpath)

        prompt_id = post_prompt(prompt)
        images = wait_for_images(prompt_id)

        # Take the last produced image by default
        last_img = images[-1]
        img_bytes = pull_image_bytes_from_comfy(last_img)
        # Guess suffix from filename
        suffix = Path(last_img.get("filename", "")).suffix or ".png"
        out_path = save_bytes_to_output(img_bytes, suffix=suffix)

        result_url = None
        if UPLOAD_PROVIDER == "catbox":
            result_url = upload_to_catbox(out_path)
        else:
            # If you don't want to upload anywhere, return the container path
            result_url = f"file://{out_path}"

        return {
            "ok": True,
            "result_url": result_url,
            "output_path": str(out_path),
            "images_meta": images
        }

    # Unknown op
    return {"ok": False, "error": f"Unknown op '{op}'."}


# Start the RunPod serverless worker (polls the queue)
runpod.serverless.start({"handler": job_handler})
