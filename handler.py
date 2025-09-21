import os
import io
import json
import time
import uuid
import base64
import logging
from typing import Optional, Dict, Any, List

import requests
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

# -----------------------
# Environment & Constants
# -----------------------
COMFY_ROOT = os.environ.get("COMFY_ROOT", "/workspace/ComfyUI")
COMFY_PORT = int(os.environ.get("COMFY_PORT", "8188"))
HANDLER_PORT = int(os.environ.get("HANDLER_PORT", "8000"))
WORKFLOW_PATH = os.environ.get("WORKFLOW_PATH", "/workspace/comfyui/workflows/APIAutoFaceACE.json")
UPLOAD_PROVIDER = os.environ.get("UPLOAD_PROVIDER", "catbox").lower()

INPUT_DIR = os.path.join(COMFY_ROOT, "input")
OUTPUT_DIR = os.path.join(COMFY_ROOT, "output")

COMFY_API = f"http://127.0.0.1:{COMFY_PORT}"

os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

log = logging.getLogger("handler")
logging.basicConfig(level=logging.INFO)


# -----------
# FastAPI I/O
# -----------
class InputModel(BaseModel):
    op: str = "faceswap"
    # images by URL only (as requested)
    source_url: Optional[str] = None
    target_url: Optional[str] = None


class OutputModel(BaseModel):
    ok: bool
    message: str
    images: List[str] = []               # local paths (absolute)
    image_urls: List[str] = []           # public URLs (uploaded)
    prompt_id: Optional[str] = None
    node_results: Optional[dict] = None


app = FastAPI(title="comfylifeforlife")


# --------------------
# Utility / HTTP calls
# --------------------
def comfy_get(path: str, **kwargs):
    r = requests.get(f"{COMFY_API}{path}", timeout=kwargs.pop("timeout", 30), **kwargs)
    r.raise_for_status()
    return r.json()


def comfy_post(path: str, json_body: dict, **kwargs):
    r = requests.post(f"{COMFY_API}{path}", json=json_body, timeout=kwargs.pop("timeout", 60), **kwargs)
    r.raise_for_status()
    return r.json()


def download_to_input(url: str, prefix: str) -> str:
    """Download an image URL into ComfyUI/input and return the saved filename (basename only)."""
    ext = ".png"
    for cand in [".png", ".jpg", ".jpeg", ".webp"]:
        if url.lower().split("?")[0].endswith(cand):
            ext = cand
            break

    fname = f"{prefix}_{uuid.uuid4().hex}{ext}"
    dst = os.path.join(INPUT_DIR, fname)

    log.info(f"[download] GET {url}")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    log.info(f"[download] saved to {dst}")
    return fname  # return just the basename for LoadImage


def patch_workflow(source_basename: str, target_basename: str) -> Dict[str, Any]:
    """
    Open the saved API-format workflow and replace the two first image-loader nodes with our filenames.
    We set the 'image' input to the *basename* (ComfyUI LoadImage reads from input/).
    """
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        wf = json.load(f)

    replaced = 0

    def maybe_patch(node: Dict[str, Any], which: str) -> bool:
        ctype = str(node.get("class_type", "")).lower()
        # Support common loaders: "LoadImage", "Image Load", "ImagePathLoader" (if present)
        if "load" in ctype and "image" in ctype or ctype in ("loadimage", "imageloader", "imagepathloader"):
            inputs = node.setdefault("inputs", {})
            inputs["image"] = which  # basename relative to input/
            return True
        return False

    # Formats: some files store nodes under "nodes" list; others under "graph" dict.
    nodes_obj = None
    if isinstance(wf, dict) and "nodes" in wf and isinstance(wf["nodes"], list):
        nodes_obj = wf["nodes"]
    elif isinstance(wf, dict) and "graph" in wf and isinstance(wf["graph"], dict) and "nodes" in wf["graph"]:
        nodes_obj = wf["graph"]["nodes"]

    if not nodes_obj:
        raise RuntimeError("Workflow JSON doesn't look like a ComfyUI API workflow (missing nodes).")

    for node in nodes_obj:
        if replaced == 0 and maybe_patch(node, source_basename):
            replaced = 1
            continue
        if replaced == 1 and maybe_patch(node, target_basename):
            replaced = 2
            break

    if replaced < 2:
        raise RuntimeError("Could not find two image loader nodes to patch in the workflow.")

    return wf


def wait_for_results(prompt_id: str, timeout_s: int = 300) -> Dict[str, Any]:
    """Poll /history/{prompt_id} until results land or timeout."""
    start = time.time()
    while True:
        try:
            hist = comfy_get(f"/history/{prompt_id}", timeout=30)
        except Exception:
            hist = None

        if isinstance(hist, dict) and prompt_id in hist:
            item = hist[prompt_id]
            # results appear under item["outputs"][node_id]["images"]
            return item

        if time.time() - start > timeout_s:
            raise TimeoutError(f"Timed out waiting for results for prompt {prompt_id}")

        time.sleep(1)


def collect_output_paths(history_entry: Dict[str, Any]) -> List[str]:
    """
    Rebuild absolute filesystem paths from history images.
    ComfyUI records: {"filename": "...", "subfolder": "...", "type": "output"}
    """
    paths: List[str] = []
    outputs = history_entry.get("outputs", {})
    for _node, node_data in outputs.items():
        for img in node_data.get("images", []):
            name = img.get("filename")
            subfolder = img.get("subfolder") or ""
            # Always under ComfyUI/output
            full = os.path.join(OUTPUT_DIR, subfolder, name)
            if os.path.isfile(full):
                paths.append(full)
    # de-dupe preserve order
    seen = set()
    uniq = []
    for p in paths:
        if p not in seen:
            uniq.append(p)
            seen.add(p)
    return uniq


def upload_public(filepath: str) -> Optional[str]:
    """
    Upload a file to get a public URL. Default provider: catbox.
    Fallback to 0x0.st if catbox fails.
    """
    try:
        if UPLOAD_PROVIDER == "catbox":
            with open(filepath, "rb") as f:
                r = requests.post(
                    "https://catbox.moe/user/api.php",
                    data={"reqtype": "fileupload"},
                    files={"fileToUpload": (os.path.basename(filepath), f)},
                    timeout=120,
                )
            if r.ok and r.text.startswith("http"):
                return r.text.strip()

        # Fallback or alternate
        with open(filepath, "rb") as f:
            r = requests.post("https://0x0.st", files={"file": f}, timeout=120)
        if r.ok and r.text.startswith("http"):
            return r.text.strip()
    except Exception as e:
        log.warning(f"[upload] failed to upload {filepath}: {e}")

    return None


# -------------
# FastAPI routes
# -------------
@app.post("/")
def run(input: InputModel) -> OutputModel:
    # Simple health check that does NOT require images
    if input.op == "health_check":
        try:
            comfy_ok = False
            try:
                _ = comfy_get("/system_stats", timeout=5)
                comfy_ok = True
            except Exception:
                pass
            return OutputModel(ok=True, message="ok", images=[], image_urls=[], prompt_id=None,
                               node_results={"comfy_up": comfy_ok})
        except Exception as e:
            return OutputModel(ok=False, message=f"health_check error: {e}", images=[], image_urls=[])

    # Faceswap op requires both URLs
    if input.op.lower() in ("faceswap", "swap", "face_swap"):
        if not input.source_url or not input.target_url:
            return OutputModel(ok=False, message="Provide both 'source_url' and 'target_url'.", images=[], image_urls=[])

        try:
            # 1) Download into ComfyUI/input
            source_basename = download_to_input(input.source_url, "src")
            target_basename = download_to_input(input.target_url, "tgt")

            # 2) Patch workflow
            wf = patch_workflow(source_basename, target_basename)

            # 3) Send prompt
            resp = comfy_post("/prompt", json_body={"prompt": wf}, timeout=60)
            prompt_id = resp.get("prompt_id") or resp.get("promptId") or resp.get("id")
            if not prompt_id:
                return OutputModel(ok=False, message="ComfyUI /prompt returned no prompt_id", images=[], image_urls=[])

            # 4) Wait for results
            hist = wait_for_results(prompt_id, timeout_s=600)

            # 5) Collect output file paths and upload
            paths = collect_output_paths(hist)
            urls = []
            for p in paths:
                url = upload_public(p)
                if url:
                    urls.append(url)

            msg = "faceswap complete" if paths else "no output images found"
            return OutputModel(ok=bool(paths), message=msg, images=paths, image_urls=urls,
                               prompt_id=prompt_id, node_results=hist.get("outputs", {}))

        except Exception as e:
            log.exception("faceswap failed")
            return OutputModel(ok=False, message=f"faceswap error: {e}", images=[], image_urls=[])

    # Unknown op
    return OutputModel(ok=False, message=f"Unknown op '{input.op}'", images=[], image_urls=[])


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=HANDLER_PORT)
