import os
import io
import time
import json
import uuid
import base64
from typing import Optional, Literal, Dict, Any, List

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl

# ----------------------------
# Configuration (env w/ sane defaults)
# ----------------------------
COMFY_HOST = os.getenv("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.getenv("COMFY_PORT", "8188"))
COMFY_BASE = f"http://{COMFY_HOST}:{COMFY_PORT}"
COMFY_WORKFLOW_PATH = os.getenv(
    "WORKFLOW_PATH",
    os.path.join(os.path.dirname(__file__), "comfyui", "workflows", "APIAutoFaceACE.json"),
)
COMFY_READY_TIMEOUT = int(os.getenv("COMFY_READY_TIMEOUT", "120"))  # seconds

# Where to stash temp inputs
TMP_DIR = os.getenv("TMP_DIR", "/tmp")

# Upload provider (we'll default to Catbox which requires no API key)
UPLOAD_PROVIDER = os.getenv("UPLOAD_PROVIDER", "catbox")  # only 'catbox' supported here

# ----------------------------
# FastAPI app
# ----------------------------
app = FastAPI(title="comfylifeforlife serverless handler", version="1.0.0")

# ----------------------------
# Models
# ----------------------------
class HealthPayload(BaseModel):
    op: Literal["health_check"] = "health_check"

class SwapFacesPayload(BaseModel):
    op: Literal["swap_faces"] = "swap_faces"
    # Provide either URLs or base64s; we will only use URLs in production as requested,
    # but keep base64 support for flexibility.
    source_url: Optional[HttpUrl] = None
    target_url: Optional[HttpUrl] = None
    source_b64: Optional[str] = None
    target_b64: Optional[str] = None
    # Optional runtime tweakables
    workflow_path: Optional[str] = None
    timeout: Optional[int] = 600  # seconds

class RunPayload(BaseModel):
    # Generic wrapper – op decides which payload we validate at runtime
    op: Literal["health_check", "swap_faces"] = "health_check"
    source_url: Optional[HttpUrl] = None
    target_url: Optional[HttpUrl] = None
    source_b64: Optional[str] = None
    target_b64: Optional[str] = None
    workflow_path: Optional[str] = None
    timeout: Optional[int] = 600

# ----------------------------
# Utilities
# ----------------------------
def _now_ms() -> int:
    return int(time.time() * 1000)

def _ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

async def comfy_ready() -> bool:
    """Poll ComfyUI until ready or timeout."""
    deadline = time.time() + COMFY_READY_TIMEOUT
    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.time() < deadline:
            try:
                # Any of these should work; '/queue' is quite cheap.
                r = await client.get(f"{COMFY_BASE}/queue")
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            await _sleep(0.5)
    return False

async def _sleep(sec: float):
    # tiny helper to keep all awaits in one place
    await httpx.AsyncClient().aclose()  # no-op to keep linter calm
    time.sleep(sec)

async def _download_image_to_tmp(url: str, name: str) -> str:
    _ensure_dir(TMP_DIR)
    out_path = os.path.join(TMP_DIR, name)
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
    return out_path

async def _write_b64_to_tmp(b64_data: str, name: str) -> str:
    _ensure_dir(TMP_DIR)
    out_path = os.path.join(TMP_DIR, name)
    raw = base64.b64decode(b64_data.split(",")[-1])
    with open(out_path, "wb") as f:
        f.write(raw)
    return out_path

async def _comfy_upload_local_image(path: str) -> str:
    """Upload a local file to ComfyUI /upload/image so it appears under 'input/'."""
    filename = os.path.basename(path)
    async with httpx.AsyncClient(timeout=60.0) as client:
        with open(path, "rb") as fp:
            files = {"image": (filename, fp, "application/octet-stream")}
            r = await client.post(f"{COMFY_BASE}/upload/image", files=files)
            r.raise_for_status()
    return filename  # Comfy saves under input/{filename}

def _load_workflow(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _patch_workflow_images(workflow: Dict[str, Any], source_input_name: str, target_input_name: str) -> Dict[str, Any]:
    """
    Patch workflow so that two 'LoadImage' (or compatible) nodes use the uploaded filenames.
    Strategy:
    - Prefer nodes that already have an 'image' param.
    - If there are >2 such nodes, take the first two.
    - We assume your workflow expects two inputs: source and target.
    """
    nodes = workflow
    image_nodes = []
    for node_id, node in nodes.items():
        inputs = node.get("inputs", {})
        class_type = node.get("class_type", "")
        if "image" in inputs or class_type.lower().startswith("loadimage"):
            image_nodes.append((node_id, node))

    if len(image_nodes) < 2:
        # Be explicit so we know what broke if the workflow changes
        raise HTTPException(status_code=422, detail="Workflow must contain at least two image input nodes.")

    # Patch the first two we find: [0] -> source, [1] -> target
    image_nodes[0][1].setdefault("inputs", {})["image"] = source_input_name
    image_nodes[1][1].setdefault("inputs", {})["image"] = target_input_name
    return nodes

async def _comfy_submit_prompt(prompt_json: Dict[str, Any]) -> str:
    """POST /prompt and return prompt_id."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(f"{COMFY_BASE}/prompt", json={"prompt": prompt_json, "client_id": str(uuid.uuid4())})
        r.raise_for_status()
        data = r.json()
        return data.get("prompt_id") or data.get("promptId") or data.get("id") or ""

async def _comfy_wait_for_images(prompt_id: str, timeout: int = 600) -> List[Dict[str, Any]]:
    """
    Poll /history/{prompt_id} until images appear.
    Returns the list of produced images (each item has filename, type, subfolder).
    """
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=10.0) as client:
        while time.time() < deadline:
            r = await client.get(f"{COMFY_BASE}/history/{prompt_id}")
            if r.status_code == 200:
                hist = r.json()
                # Structure: { "<prompt_id>": { "outputs": { "<node_id>": { "images": [ ... ] } } } }
                entry = hist.get(prompt_id) or {}
                outputs = entry.get("outputs") or {}
                imgs: List[Dict[str, Any]] = []
                for _node_id, info in outputs.items():
                    for im in info.get("images", []) or []:
                        imgs.append(im)
                if imgs:
                    return imgs
            await _sleep(0.5)
    raise HTTPException(status_code=504, detail="Timed out waiting for Comfy output.")

async def _comfy_fetch_image_bytes(image_meta: Dict[str, Any]) -> bytes:
    """
    Download one output image from ComfyUI's /view endpoint.
    """
    filename = image_meta["filename"]
    subfolder = image_meta.get("subfolder", "")
    img_type = image_meta.get("type", "output")

    params = {"filename": filename, "type": img_type}
    if subfolder:
        params["subfolder"] = subfolder

    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.get(f"{COMFY_BASE}/view", params=params)
        r.raise_for_status()
        return r.content

async def _upload_bytes_to_catbox(image_bytes: bytes, filename: str = "output.png") -> str:
    """
    Uploads bytes to Catbox, returns public URL.
    Doc: https://catbox.moe/tools.php (user API)
    """
    files = {"fileToUpload": (filename, io.BytesIO(image_bytes), "application/octet-stream")}
    data = {"reqtype": "fileupload"}
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post("https://catbox.moe/user/api.php", data=data, files=files)
        r.raise_for_status()
        url = r.text.strip()
        if not url.startswith("http"):
            raise HTTPException(status_code=502, detail=f"Catbox returned unexpected response: {url}")
        return url

# ----------------------------
# Routes
# ----------------------------
@app.get("/healthz")
async def healthz():
    # No Comfy check required; super-fast readiness for the platform.
    return {"status": "ok", "ts": _now_ms()}

@app.post("/run")
async def run(payload: RunPayload):
    """
    RunPod Serverless entrypoint.
    Supports:
    - {"op":"health_check"}
    - {"op":"swap_faces", "source_url":"...", "target_url":"..."}  (URLs only, as requested)
    """
    # Always allow health check with no extra inputs.
    if payload.op == "health_check":
        return {"status": "ok", "ts": _now_ms()}

    # From here, we need Comfy up.
    if not await comfy_ready():
        raise HTTPException(status_code=503, detail="ComfyUI not ready.")

    # Validate swap inputs
    if payload.op == "swap_faces":
        # Prefer URLs; allow b64 fallback if needed.
        if not ((payload.source_url and payload.target_url) or (payload.source_b64 and payload.target_b64)):
            raise HTTPException(status_code=400, detail="Provide 'source_url' & 'target_url' (or 'source_b64' & 'target_b64').")

        # Resolve workflow path
        wf_path = payload.workflow_path or COMFY_WORKFLOW_PATH
        if not os.path.isfile(wf_path):
            raise HTTPException(status_code=500, detail=f"Workflow not found at {wf_path}")

        # Prepare inputs
        if payload.source_url and payload.target_url:
            src_local = await _download_image_to_tmp(str(payload.source_url), f"src_{uuid.uuid4().hex}.png")
            tgt_local = await _download_image_to_tmp(str(payload.target_url), f"tgt_{uuid.uuid4().hex}.png")
        else:
            src_local = await _write_b64_to_tmp(payload.source_b64, f"src_{uuid.uuid4().hex}.png")
            tgt_local = await _write_b64_to_tmp(payload.target_b64, f"tgt_{uuid.uuid4().hex}.png")

        # Upload into Comfy's input/ so 'LoadImage' nodes can see them
        src_name = await _comfy_upload_local_image(src_local)  # e.g. "src_xxx.png"
        tgt_name = await _comfy_upload_local_image(tgt_local)  # e.g. "tgt_xxx.png"

        # Load and patch the workflow
        workflow = _load_workflow(wf_path)
        prompt_json = _patch_workflow_images(workflow, source_input_name=src_name, target_input_name=tgt_name)

        # Submit prompt and wait for images
        prompt_id = await _comfy_submit_prompt(prompt_json)
        images = await _comfy_wait_for_images(prompt_id, timeout=payload.timeout or 600)

        # Take the first image
        out_bytes = await _comfy_fetch_image_bytes(images[0])

        # Upload to Catbox (returns a public URL)
        if UPLOAD_PROVIDER == "catbox":
            public_url = await _upload_bytes_to_catbox(out_bytes, filename="faceswap.png")
        else:
            # Fallback to data URL if someone disables catbox
            b64 = base64.b64encode(out_bytes).decode("utf-8")
            public_url = f"data:image/png;base64,{b64}"

        return {
            "status": "ok",
            "output_url": public_url,
            "debug": {
                "prompt_id": prompt_id,
                "comfy_base": COMFY_BASE,
            }
        }

    # Unknown op
    raise HTTPException(status_code=400, detail=f"Unknown op '{payload.op}'")
