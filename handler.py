import os
import io
import json
import time
import uuid
import shutil
import pathlib
import requests
from typing import Dict, Any, Tuple, Optional
from fastapi import FastAPI, Body
from pydantic import BaseModel
from urllib.parse import urlparse

# ------------------ Config via env ------------------
COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.environ.get("COMFY_PORT", "8188"))
COMFY_URL  = f"http://{COMFY_HOST}:{COMFY_PORT}"
CLIENT_ID  = os.environ.get("COMFY_CLIENT_ID", "runpod-serverless")

# Repo layout assumptions (adjust by env if needed)
COMFY_ROOT = os.environ.get("COMFY_ROOT", "/workspace/comfylifeforlife/comfyui")
WORKFLOW_PATH = os.environ.get(
    "WORKFLOW_PATH",
    f"{COMFY_ROOT}/workflows/APIAutoFaceACE.json"
)

# Where we store temp/downloaded inputs
TMP_DIR = os.environ.get("TMP_DIR", "/workspace/tmp")
os.makedirs(TMP_DIR, exist_ok=True)

# Where ComfyUI will save images (ensure your SaveImage node points here or default)
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/workspace/output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Optional: explicitly tell which nodes to patch (if known)
# If you know the node ids in APIAutoFaceACE.json that accept the source/target images, set them here.
SRC_NODE_ID = os.environ.get("SRC_NODE_ID", "")  # e.g. "12"
TGT_NODE_ID = os.environ.get("TGT_NODE_ID", "")  # e.g. "17"
IMAGE_KEY   = os.environ.get("IMAGE_KEY", "image")  # field name inside inputs for image path


# ------------------ Models ------------------
class RunInput(BaseModel):
    op: str
    source_image: Optional[str] = None  # URL or absolute path or volume path
    target_image: Optional[str] = None  # same


# ------------------ Helpers ------------------
def is_url(s: str) -> bool:
    try:
        u = urlparse(s)
        return u.scheme in ("http", "https")
    except Exception:
        return False

def download_to_tmp(url: str, prefix: str) -> str:
    r = requests.get(url, timeout=120, stream=True)
    r.raise_for_status()
    ext = ".png"
    content_type = r.headers.get("Content-Type", "")
    if "jpeg" in content_type: ext = ".jpg"
    elif "jpg" in content_type: ext = ".jpg"
    elif "webp" in content_type: ext = ".webp"
    elif "png" in content_type: ext = ".png"
    dest = os.path.join(TMP_DIR, f"{prefix}_{uuid.uuid4().hex[:8]}{ext}")
    with open(dest, "wb") as f:
        shutil.copyfileobj(r.raw, f)
    return dest

def resolve_image_path(s: str, prefix: str) -> str:
    """Accepts URL or local path; returns local filesystem path for ComfyUI."""
    if is_url(s):
        return download_to_tmp(s, prefix)
    # Allow absolute or relative paths; normalize:
    p = pathlib.Path(s)
    if not p.is_absolute():
        p = pathlib.Path("/").joinpath(s)  # in case someone passes 'data/...' make it absolute-ish
    p = p.resolve()
    if not p.exists():
        raise FileNotFoundError(f"Image path not found: {p}")
    return str(p)

def load_workflow(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Workflow JSON not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        wf = json.load(f)
    if not isinstance(wf, dict):
        raise ValueError("Workflow JSON must be a JSON object mapping node_id -> node.")
    return wf

def patch_workflow_with_images(
    wf: Dict[str, Any],
    src_path: str,
    tgt_path: str
) -> Dict[str, Any]:
    """Inject source/target image paths into the workflow.

    Strategy:
    - If SRC_NODE_ID / TGT_NODE_ID provided: set those nodes' inputs[IMAGE_KEY] = path.
    - Else: patch the first two nodes whose class_type contains 'LoadImage' (best-effort).
    """
    wf = json.loads(json.dumps(wf))  # deep copy

    def set_image(node_id: str, path: str) -> bool:
        node = wf.get(node_id)
        if not node or "inputs" not in node:
            return False
        node["inputs"][IMAGE_KEY] = path
        return True

    did_src = False
    did_tgt = False

    if SRC_NODE_ID:
        did_src = set_image(SRC_NODE_ID, src_path)
    if TGT_NODE_ID:
        did_tgt = set_image(TGT_NODE_ID, tgt_path)

    if not (did_src and did_tgt):
        # fallback: first two LoadImage-like nodes
        load_nodes = [nid for nid, n in wf.items()
                      if isinstance(n, dict) and
                         str(n.get("class_type", "")).lower().find("loadimage") >= 0]
        if not did_src and len(load_nodes) >= 1:
            did_src = set_image(load_nodes[0], src_path)
        if not did_tgt and len(load_nodes) >= 2:
            did_tgt = set_image(load_nodes[1], tgt_path)

    if not did_src or not did_tgt:
        # As last resort, try any nodes that have an 'image' input field
        if not did_src:
            for nid, n in wf.items():
                if isinstance(n, dict) and "inputs" in n and IMAGE_KEY in n["inputs"]:
                    if set_image(nid, src_path):
                        did_src = True
                        break
        if not did_tgt:
            for nid, n in wf.items():
                if isinstance(n, dict) and "inputs" in n and IMAGE_KEY in n["inputs"]:
                    if set_image(nid, tgt_path):
                        did_tgt = True
                        break

    if not (did_src and did_tgt):
        raise RuntimeError("Could not locate source/target image nodes to patch. "
                           "Set SRC_NODE_ID/TGT_NODE_ID envs or adjust patch logic.")
    return wf

def post_prompt(prompt: Dict[str, Any]) -> Dict[str, Any]:
    body = {"prompt": prompt, "client_id": CLIENT_ID}
    r = requests.post(f"{COMFY_URL}/prompt", json=body, timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f"/prompt returned {r.status_code}: {r.text}")
    return r.json()

def wait_for_result(prompt_id: str, timeout_s: int = 600) -> Dict[str, Any]:
    start = time.time()
    while True:
        r = requests.get(f"{COMFY_URL}/history/{prompt_id}", timeout=30)
        r.raise_for_status()
        h = r.json()
        if prompt_id in h:
            entry = h[prompt_id]
            if "outputs" in entry:
                return entry["outputs"]
        if time.time() - start > timeout_s:
            raise TimeoutError("Timed out waiting for ComfyUI result.")
        time.sleep(1)

# ------------------ API ------------------
app = FastAPI()

@app.post("/runs")
def runs(input: RunInput = Body(...)):
    op = (input.op or "").lower()

    if op == "health_check":
        # No Comfy calls here; used by tests.json validation
        return {"ok": True, "ts": time.time()}

    if op == "faceswap":
        if not input.source_image or not input.target_image:
            return {"error": "source_image and target_image are required."}

        try:
            src_path = resolve_image_path(input.source_image, "src")
            tgt_path = resolve_image_path(input.target_image, "tgt")
        except Exception as e:
            return {"error": f"Failed to resolve images: {e.__class__.__name__}: {e}"}

        try:
            wf = load_workflow(WORKFLOW_PATH)
        except Exception as e:
            return {"error": f"Failed to load workflow: {e.__class__.__name__}: {e}"}

        try:
            prompt = patch_workflow_with_images(wf, src_path, tgt_path)
        except Exception as e:
            return {"error": f"Failed to patch workflow: {e.__class__.__name__}: {e}"}

        try:
            pr = post_prompt(prompt)
        except Exception as e:
            return {"error": f"ComfyUI prompt error: {e.__class__.__name__}: {e}"}

        prompt_id = pr.get("prompt_id") or pr.get("promptId") or ""
        if not prompt_id:
            return {"warning": "No prompt_id returned by ComfyUI.", "raw": pr}

        try:
            outputs = wait_for_result(prompt_id, timeout_s=600)
        except Exception as e:
            return {"error": f"Waiting for result failed: {e.__class__.__name__}: {e}", "prompt_id": prompt_id}

        # Try to extract saved files (typical SaveImage structure)
        saved = []
        try:
            # outputs is a dict keyed by node_id; each has 'images' array, with name/subfolder
            for node_id, node_out in outputs.items():
                if not isinstance(node_out, dict):
                    continue
                images = node_out.get("images") or []
                for im in images:
                    # ComfyUI usually writes under ComfyUI/output; adjust join as needed
                    name = im.get("filename") or im.get("name")
                    subf = im.get("subfolder", "")
                    folder = im.get("type")  # sometimes "output"
                    # Build a best-effort full path:
                    pieces = [COMFY_ROOT, "ComfyUI", folder or "output"]
                    if subf:
                        pieces.append(subf)
                    full = str(pathlib.Path(*pieces, name).resolve())
                    saved.append({"filename": name, "path": full, "node_id": node_id})
        except Exception:
            pass

        return {
            "status": "COMPLETED",
            "prompt_id": prompt_id,
            "outputs": outputs,
            "saved": saved
        }

    return {"error": f"Unknown op: {input.op}"}
