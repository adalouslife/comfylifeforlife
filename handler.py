import os
import io
import time
import json
import uuid
import base64
import runpod
import shutil
import requests
from typing import Dict, Any

COMFY_HOST = os.getenv("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.getenv("COMFY_PORT", "8188"))
COMFY_URL  = f"http://{COMFY_HOST}:{COMFY_PORT}"

INPUT_DIR  = os.getenv("INPUT_DIR", "/workspace/ComfyUI/input")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/workspace/ComfyUI/output")

# -------- Utilities --------

def _ok() -> bool:
    try:
        r = requests.get(f"{COMFY_URL}/system_stats", timeout=5)
        return r.ok
    except Exception:
        return False

def _download(url: str, dest_path: str) -> str:
    r = requests.get(url, timeout=30, stream=True)
    r.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in r.iter_content(1024 * 1024):
            if chunk:
                f.write(chunk)
    return dest_path

def _save_b64(b64: str, dest_path: str) -> str:
    with open(dest_path, "wb") as f:
        f.write(base64.b64decode(b64))
    return dest_path

def _ensure_dirs():
    os.makedirs(INPUT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def _comfy_prompt(prompt: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sends a prompt to ComfyUI /prompt and waits for output filenames
    saved by the SaveImage node(s).
    """
    # queue the prompt
    resp = requests.post(f"{COMFY_URL}/prompt", json={"prompt": prompt}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI /prompt did not return prompt_id: {data}")

    # poll history until we see our prompt finished
    for _ in range(120):  # ~120 * 0.5s = 60s
        h = requests.get(f"{COMFY_URL}/history/{prompt_id}", timeout=10)
        if h.ok:
            hjson = h.json()
            if prompt_id in hjson and "outputs" in hjson[prompt_id]:
                return hjson[prompt_id]
        time.sleep(0.5)
    raise TimeoutError("Timed out waiting for ComfyUI prompt to finish.")

# -------- Minimal prompt that proves end-to-end output --------
# Loads the first image and saves it (passthrough). This validates wiring fast.
def _build_passthrough_prompt(input_filename: str) -> Dict[str, Any]:
    return {
        # Node IDs are strings in API format
        "1": {  # LoadImage
            "class_type": "LoadImage",
            "inputs": {
                "image": input_filename
            }
        },
        "2": {  # SaveImage
            "class_type": "SaveImage",
            "inputs": {
                "images": ["1", "IMAGE"]
            }
        }
    }

# -------- Handler --------

def handler(event):
    """
    Supported ops:
      - {"op": "health_check"}
      - {"op": "faceswap", "source_url" or "source_b64", "target_url" or "target_b64"}
        (currently passthrough to validate E2E; swaps will be wired next)
    """
    _ensure_dirs()
    inp = event.get("input", {}) if isinstance(event, dict) else {}
    op = inp.get("op") or inp.get("operation")

    if op == "health_check":
        return {"ok": _ok(), "comfy_url": COMFY_URL}

    if op == "faceswap":
        # Download (or decode) the two images
        sid = f"src_{uuid.uuid4().hex}.png"
        tid = f"tgt_{uuid.uuid4().hex}.png"
        src_path = os.path.join(INPUT_DIR, sid)
        tgt_path = os.path.join(INPUT_DIR, tid)

        try:
            if "source_url" in inp:
                _download(inp["source_url"], src_path)
            elif "source_b64" in inp:
                _save_b64(inp["source_b64"], src_path)
            else:
                return {"error": "Provide 'source_url' or 'source_b64'."}

            if "target_url" in inp:
                _download(inp["target_url"], tgt_path)
            elif "target_b64" in inp:
                _save_b64(inp["target_b64"], tgt_path)
            else:
                return {"error": "Provide 'target_url' or 'target_b64'."}
        except Exception as e:
            return {"error": f"Failed to fetch inputs: {e}"}

        # For now: passthrough the SOURCE image -> SaveImage, to verify the pipeline.
        # After confirmation, we’ll replace this with your actual faceswap workflow.
        try:
            prompt = _build_passthrough_prompt(sid)
            result = _comfy_prompt(prompt)
            # Gather saved files
            saved = []
            outputs = result.get("outputs", {})
            for node_id, node_out in outputs.items():
                # SaveImage writes under output dir; the API returns file info under "images"
                images = node_out.get("images") or []
                for im in images:
                    # Comfy returns {"filename": "...", "subfolder": "...", "type": "output"}
                    filename = im.get("filename")
                    if filename:
                        saved.append({
                            "filename": filename,
                            "path": os.path.join(OUTPUT_DIR, filename)
                        })
            return {
                "ok": True,
                "note": "Passthrough complete (this proves E2E). Swap wiring comes next.",
                "outputs": saved
            }
        except Exception as e:
            return {"error": f"ComfyUI prompt failed: {e}"}

    # default
    return {"error": f"Unknown op '{op}'."}

# Start the RunPod job loop
runpod.serverless.start({"handler": handler})
