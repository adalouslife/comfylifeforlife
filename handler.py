import base64
import io
import json
import os
import time
import uuid
from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
import runpod

COMFY_HOST = os.getenv("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.getenv("COMFY_PORT", "8188"))
COMFY_URL = f"http://{COMFY_HOST}:{COMFY_PORT}"
INPUT_DIR = Path(os.getenv("INPUT_DIR", "/workspace/inputs"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/workspace/outputs"))
WORKFLOW_PATH = Path(os.getenv("WORKFLOW_JSON", "/workspace/ComfyUI/workflows/APIAutoFaceACE.json"))

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "curl/8.5.0 (RunPod-Serverless-ComfyUI)",
    "Connection": "close",
})

def _poll_comfy(timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = SESSION.get(f"{COMFY_URL}/system_stats", timeout=3)
            if r.ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError(f"ComfyUI did not come up in time on {COMFY_URL}")

@retry(wait=wait_fixed(1), stop=stop_after_attempt(3), reraise=True)
def _http_get(url, timeout=10):
    return SESSION.get(url, timeout=timeout, stream=True)

def _download_image(url: str) -> bytes:
    # First try regular requests with retries
    try:
        with _http_get(url) as r:
            r.raise_for_status()
            return r.content
    except Exception:
        # Fallback to curl (some hosts close on requests)
        import subprocess, tempfile
        with tempfile.NamedTemporaryFile() as tf:
            cmd = ["curl", "-L", "-m", "20", "-sS", "-A", "curl/8.5.0", "-o", tf.name, url]
            proc = subprocess.run(cmd, capture_output=True)
            if proc.returncode != 0:
                raise RuntimeError(f"curl failed fetching {url}: {proc.stderr.decode(errors='ignore')}")
            return Path(tf.name).read_bytes()

def _upload_image_to_comfy(name: str, data: bytes):
    files = {"image": (name, data)}
    res = SESSION.post(f"{COMFY_URL}/upload/image", files=files, timeout=20)
    if not res.ok:
        raise RuntimeError(f"Comfy upload failed: {res.status_code} {res.text}")
    # Comfy stores upload under input/; we just need the filename reference
    return name

def _queue_prompt(prompt: dict):
    r = SESSION.post(f"{COMFY_URL}/prompt", json={"prompt": prompt}, timeout=20)
    r.raise_for_status()
    return r.json()["prompt_id"]

def _wait_for_image(prompt_id: str, wait_s=120):
    start = time.time()
    while time.time() - start < wait_s:
        # List histories
        h = SESSION.get(f"{COMFY_URL}/history/{prompt_id}", timeout=10)
        if h.ok:
            hist = h.json()
            if prompt_id in hist:
                outputs = hist[prompt_id].get("outputs") or {}
                # Scan for first image in outputs
                for node_id, node_out in outputs.items():
                    images = node_out.get("images") or []
                    if images:
                        # Return first image info (Comfy serves static files)
                        item = images[0]
                        subfolder = item.get("subfolder","")
                        filename = item["filename"]
                        return f"{COMFY_URL}/view?filename={filename}&subfolder={subfolder}&type=output"
        time.sleep(1)
    raise RuntimeError("Timed out waiting for image.")

def _load_workflow():
    if not WORKFLOW_PATH.exists():
        raise FileNotFoundError(f"Workflow not found at {WORKFLOW_PATH}")
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def _apply_inputs_to_workflow(workflow: dict, source_name: str, target_name: str):
    """
    Minimal patcher:
    - find any LoadImage nodes and set their 'image' fields to the uploaded names.
    - If your graph uses specific node titles, adjust matching accordingly.
    """
    # Heuristic: map first two LoadImage nodes to source/target.
    load_nodes = [ (nid, n) for nid, n in workflow.items()
                   if n.get("class_type") in ("LoadImage", "Load Image") ]
    if len(load_nodes) < 2:
        # Try KSampler-like graphs where inputs named differently — user may need to fine-tune.
        pass

    if load_nodes:
        # First is source, second is target
        if len(load_nodes) >= 1:
            load_nodes[0][1]["inputs"]["image"] = source_name
        if len(load_nodes) >= 2:
            load_nodes[1][1]["inputs"]["image"] = target_name
    return workflow

# ---------------- RunPod handler ----------------

def handler(event):
    op = (event.get("input") or {}).get("op") or (event.get("input") or {}).get("operation")
    if not op:
        return {"ok": False, "error": "Missing 'op'."}

    if op in ("health_check", "ping"):
        try:
            _poll_comfy(timeout=30)
            return {"ok": True, "comfy_url": COMFY_URL}
        except Exception as e:
            return {"ok": False, "error": f"{e}"}

    if op == "faceswap":
        payload = event["input"]
        source_url = payload.get("source_url")
        target_url = payload.get("target_url")
        if not source_url or not target_url:
            return {"ok": False, "error": "Provide 'source_url' and 'target_url'."}

        # Ensure Comfy is alive
        _poll_comfy(timeout=60)

        # Download inputs (robust)
        try:
            src_bytes = _download_image(source_url)
            tgt_bytes = _download_image(target_url)
        except Exception as e:
            return {"ok": False, "error": f"Failed to fetch inputs: {e}"}

        # Name uploads
        src_name = f"src_{uuid.uuid4().hex}.png"
        tgt_name = f"tgt_{uuid.uuid4().hex}.png"

        # Upload to Comfy
        try:
            _upload_image_to_comfy(src_name, src_bytes)
            _upload_image_to_comfy(tgt_name, tgt_bytes)
        except Exception as e:
            return {"ok": False, "error": f"Upload failed: {e}"}

        # Build prompt from workflow
        try:
            wf = _load_workflow()
            wf = _apply_inputs_to_workflow(wf, src_name, tgt_name)
            prompt_id = _queue_prompt(wf)
            img_url = _wait_for_image(prompt_id, wait_s=180)
            return {"ok": True, "result_url": img_url}
        except Exception as e:
            return {"ok": False, "error": f"Workflow run failed: {e}"}

    return {"ok": False, "error": f"Unknown op '{op}'."}

runpod.serverless.start({"handler": handler})
