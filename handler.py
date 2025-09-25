import os
import io
import json
import time
import uuid
import runpod
import base64
import logging
import requests
from typing import Dict, Any
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ------------ Env ------------
COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.environ.get("COMFY_PORT", "8188"))
COMFY_URL  = f"http://{COMFY_HOST}:{COMFY_PORT}"

INPUT_DIR  = os.environ.get("INPUT_DIR", "/workspace/inputs")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/workspace/outputs")
WORKFLOW_PATH = os.environ.get("WORKFLOW_PATH", "/workspace/comfyui/workflows/APIAutoFaceACE.json")

# Optional: upload result to Catbox for a public URL
USE_CATBOX = os.environ.get("USE_CATBOX", "false").lower() == "true"

# ------------ Logging ------------
logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

# ------------ HTTP helpers ------------
session = requests.Session()
session.headers.update({"Accept": "application/json"})

def _comfy(path: str) -> str:
    return f"{COMFY_URL.rstrip('/')}/{path.lstrip('/')}"

def _now_ms() -> int:
    return int(time.time() * 1000)

@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.8, min=0.5, max=6))
def _http_get(url: str, **kw) -> requests.Response:
    return session.get(url, timeout=30, **kw)

@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.8, min=0.5, max=6))
def _http_post(url: str, **kw) -> requests.Response:
    return session.post(url, timeout=60, **kw)

# ----------- Catbox upload -----------
@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def _catbox_upload(file_path: str) -> str:
    # https://catbox.moe/tools.php (simple upload)
    with open(file_path, "rb") as f:
        files = {'fileToUpload': (os.path.basename(file_path), f)}
        data = {'reqtype': 'fileupload'}
        r = requests.post("https://catbox.moe/user/api.php", files=files, data=data, timeout=60)
    r.raise_for_status()
    url = r.text.strip()
    if not url.startswith("https://"):
        raise RuntimeError(f"Unexpected catbox response: {url[:200]}")
    return url

# ----------- Utilities -----------
def _basename_from_url(u: str) -> str:
    name = u.split("?")[0].rstrip("/").split("/")[-1] or f"file-{uuid.uuid4().hex}"
    # strip any accidental folder traversal
    name = name.replace("..", "").replace("/", "_")
    return name

@retry(reraise=True, stop=stop_after_attempt(4), wait=wait_exponential(multiplier=0.6, min=0.5, max=6))
def _download_to(url: str, dest_dir: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    fname = _basename_from_url(url)
    dest = os.path.join(dest_dir, fname)
    log.info(f"Downloading {url} -> {dest}")
    with session.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 15):
                if chunk:
                    f.write(chunk)
    return dest

def _load_workflow() -> Dict[str, Any]:
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        wf = json.load(f)
    if "nodes" not in wf:
        # support Comfy's graph format wrapped with 'workflow' sometimes
        wf = wf.get("workflow", wf)
    return wf

def _patch_workflow_images(wf: Dict[str, Any], src_basename: str, face_basename: str) -> Dict[str, Any]:
    """
    Find first two LoadImage nodes and set their 'image' values to the basenames
    that were saved into ComfyUI input directory. This matches your APIAutoFaceACE.json.
    """
    load_nodes = [n for n in wf.get("nodes", []) if n.get("class_type") in ("LoadImage", "LoadImageMask", "Image Load", "Load Image")]  # be generous
    if len(load_nodes) < 2:
        raise ValueError("Workflow must contain at least two LoadImage nodes (source + face).")

    # By your saved graph: node 420 = source, node 240 = face
    # But we still assign deterministically: the one whose current value contains 'newfaces' (or 'face') gets face.
    def wants_face(node):
        d = node.get("inputs", {}).get("image", "")
        return any(k in str(d).lower() for k in ("newfaces", "face", "target"))

    face_node = next((n for n in load_nodes if wants_face(n)), load_nodes[0])
    remaining = [n for n in load_nodes if n is not face_node]
    src_node = remaining[0]

    # Patch
    face_node.setdefault("inputs", {})["image"] = face_basename
    src_node.setdefault("inputs", {})["image"]  = src_basename
    return wf

def _queue_prompt(wf: Dict[str, Any]) -> str:
    payload = {"prompt": wf}
    r = _http_post(_comfy("/prompt"), json=payload)
    r.raise_for_status()
    data = r.json()
    prompt_id = data.get("prompt_id") or data.get("promptId") or data.get("id")
    if not prompt_id:
        raise RuntimeError(f"Missing prompt_id in response: {data}")
    return prompt_id

def _collect_output(prompt_id: str, timeout_s: int = 300) -> Dict[str, Any]:
    """
    Poll /history/{id} until images are available or timeout.
    """
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        resp = _http_get(_comfy(f"/history/{prompt_id}"))
        if resp.status_code == 200:
            j = resp.json()
            # Comfy returns { id: { "outputs": { node_id: { "images": [...] } } } }
            entry = j.get(prompt_id) or j
            outputs = (entry or {}).get("outputs", {})
            any_images = []
            for node_id, out in outputs.items():
                imgs = out.get("images") or []
                for im in imgs:
                    # Each record has "filename" that's already saved under OUTPUT_DIR
                    if "filename" in im:
                        any_images.append(os.path.join(OUTPUT_DIR, im["filename"]))
            if any_images:
                return {"images": any_images}
        time.sleep(1.0)

    raise TimeoutError(f"Timed out waiting for output of prompt {prompt_id}")

# ----------- Ops -----------
def op_ping(_: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True}

def op_health_check(_: Dict[str, Any]) -> Dict[str, Any]:
    try:
        r = _http_get(_comfy("/system_stats"))
        r.raise_for_status()
        return {"ok": True, "stats": r.json()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def op_faceswap(inp: Dict[str, Any]) -> Dict[str, Any]:
    """
    input:
    {
      "source_url": "https://...",
      "face_url": "https://...",
      "upload": true|false   # optional: upload to Catbox and return a public URL
    }
    """
    source_url = inp.get("source_url")
    face_url   = inp.get("face_url")
    if not source_url or not face_url:
        raise ValueError("Provide both 'source_url' and 'face_url'.")

    # 1) fetch inputs with retries
    try:
        src_path  = _download_to(source_url, INPUT_DIR)
        face_path = _download_to(face_url,   INPUT_DIR)
    except Exception as e:
        raise RuntimeError(f"Failed to fetch inputs: {e}")

    src_base  = os.path.basename(src_path)
    face_base = os.path.basename(face_path)

    # 2) load + patch workflow
    wf = _load_workflow()
    wf = _patch_workflow_images(wf, src_base, face_base)

    # 3) queue prompt
    prompt_id = _queue_prompt(wf)

    # 4) collect output
    result = _collect_output(prompt_id, timeout_s=int(os.environ.get("COMFY_TIMEOUT", "480")))

    # 5) publish URLs if requested
    image_paths = result["images"]
    public_urls = []
    if USE_CATBOX or str(inp.get("upload")).lower() == "true":
        for p in image_paths:
            try:
                public_urls.append(_catbox_upload(p))
            except Exception as e:
                log.warning(f"Catbox upload failed for {p}: {e}")

    return {
        "ok": True,
        "output_paths": image_paths,
        "urls": public_urls
    }

# ---------- Router ----------
OP_MAP = {
    "ping": op_ping,
    "health_check": op_health_check,
    "faceswap": op_faceswap
}

def handler(event):
    """RunPod handler entrypoint."""
    body = event.get("input") or {}
    op = (body.get("op") or body.get("operation") or "").lower().strip()
    if not op:
        # Keep tests decoupled from your repo build
        # A no-op path returns success quickly.
        return {"ok": True, "message": "no-op"}

    fn = OP_MAP.get(op)
    if not fn:
        return {"ok": False, "error": f"Unknown op '{op}'"}

    try:
        return fn(body)
    except Exception as e:
        # Surface a readable error and avoid worker crash
        return {"ok": False, "error": str(e)}

runpod.serverless.start({"handler": handler})
