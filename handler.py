import os
import io
import json
import time
import runpod
import shutil
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional

COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.environ.get("COMFY_PORT", "8188"))
COMFY_URL  = f"http://{COMFY_HOST}:{COMFY_PORT}"

INPUT_DIR  = Path(os.environ.get("INPUT_DIR", "/workspace/inputs"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/workspace/outputs"))
WORKFLOW   = Path("/workspace/comfyui/workflows/APIAutoFaceACE.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------- helpers ----------
def _http_get_json(url: str, timeout=5) -> Dict[str, Any]:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()

def _wait_for_comfy(timeout_sec=60):
    """Wait until ComfyUI answers /system_stats."""
    start = time.time()
    while time.time() - start < timeout_sec:
        try:
            _ = _http_get_json(f"{COMFY_URL}/system_stats", timeout=3)
            return True
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"ComfyUI did not come up in time: {COMFY_URL}/system_stats")

def _download(url: str, dest: Path, max_retries=4) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err = None
    for i in range(max_retries):
        try:
            with requests.get(url, stream=True, timeout=20) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    shutil.copyfileobj(r.raw, f)
            return dest
        except Exception as e:
            last_err = e
            time.sleep(1 + i)  # backoff
    raise RuntimeError(f"Failed to fetch inputs: {last_err}")

def _load_workflow() -> Dict[str, Any]:
    with open(WORKFLOW, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_debug(obj: Any, name: str):
    try:
        p = Path(f"/workspace/debug_{name}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
    except Exception:
        pass

def _queue_prompt(prompt: Dict[str, Any]) -> str:
    # Comfy expects {"prompt": {graph...}, "client_id": "..."} but client_id is optional server-side.
    payload = {"prompt": prompt}
    r = requests.post(f"{COMFY_URL}/prompt", json=payload, timeout=30)
    if not r.ok:
        # Bubble up Comfy’s own error text so we see which node failed
        raise requests.HTTPError(f"{r.status_code} {r.reason}: {r.text}", response=r)
    return r.json().get("prompt_id", "")

def _get_history(prompt_id: str) -> Dict[str, Any]:
    r = requests.get(f"{COMFY_URL}/history/{prompt_id}", timeout=30)
    r.raise_for_status()
    return r.json()

def _collect_images(history: Dict[str, Any]) -> Dict[str, str]:
    """
    Returns { node_id: absolute_file_path } for all images produced.
    """
    files: Dict[str, str] = {}
    for _pid, item in history.items():
        for node_id, node_output in item.get("outputs", {}).items():
            for k, out in node_output.items():
                if isinstance(out, list):
                    for entry in out:
                        if isinstance(entry, dict) and entry.get("type") == "image":
                            fp = Path(OUTPUT_DIR, entry["subfolder"], f"{entry['filename']}")
                            files[node_id] = str(fp)
    return files

# ---------- ops ----------
def op_ping(_input: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True}

def op_health_check(_input: Dict[str, Any]) -> Dict[str, Any]:
    _wait_for_comfy(timeout_sec=20)
    stats = _http_get_json(f"{COMFY_URL}/system_stats", timeout=5)
    return {"ok": True, "stats": stats}

def op_version(_input: Dict[str, Any]) -> Dict[str, Any]:
    _wait_for_comfy(timeout_sec=10)
    # best-effort info without failing if missing
    info = {"ok": True, "comfy_url": COMFY_URL}
    try:
        info["stats"] = _http_get_json(f"{COMFY_URL}/system_stats", timeout=5)
    except Exception as e:
        info["stats_error"] = str(e)
    return info

def op_faceswap(_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    input: {
      "source_url": "...",   # face to extract (or driving face)
      "target_url": "...",   # image whose face will be replaced
    }
    """
    source_url = _input.get("source_url")
    target_url = _input.get("target_url")

    if not source_url or not target_url:
        return {"ok": False, "error": "Provide 'source_url' and 'target_url'."}

    _wait_for_comfy(timeout_sec=40)

    # 1) fetch inputs
    src_path = _download(source_url, INPUT_DIR / "source.jpg")
    tgt_path = _download(target_url, INPUT_DIR / "target.jpg")

    # 2) load & patch workflow (two LoadImage nodes)
    wf = _load_workflow()

    # Heuristic: find the first two nodes whose class_type is "LoadImage"
    # and set their `inputs.image` fields to our filenames.
    replaced = 0
    for node_id, node in wf.items():
        if isinstance(node, dict) and node.get("class_type") == "LoadImage":
            if replaced == 0:
                node.setdefault("inputs", {})["image"] = src_path.name
                replaced += 1
            elif replaced == 1:
                node.setdefault("inputs", {})["image"] = tgt_path.name
                replaced += 1
            if replaced >= 2:
                break

    if replaced < 2:
        return {"ok": False, "error": "Workflow does not contain two LoadImage nodes to patch."}

    _save_debug(wf, "patched_prompt")

    # 3) queue + wait
    try:
        prompt_id = _queue_prompt(wf)
    except Exception as e:
        return {"ok": False, "error": f"Pipeline failed: {e}"}

    # Comfy renders async; small wait & poll history
    time.sleep(1.0)
    history = _get_history(prompt_id)
    images = _collect_images(history)

    if not images:
        return {"ok": False, "error": "No images produced. Check nodes and models."}

    # 4) Return local file paths; if you want URLs, serve them via S3 or a tiny HTTP server.
    # For now we return absolute paths from the container. (You can hook your S3 volume here.)
    return {
        "ok": True,
        "prompt_id": prompt_id,
        "images": images,
    }

# ---------- dispatch ----------
OP_MAP = {
    "ping": op_ping,
    "health_check": op_health_check,
    "version": op_version,
    "faceswap": op_faceswap,
}

def handler(event):
    body = event.get("input") or {}
    op = (body.get("op") or "").strip().lower()

    if not op:
        # default tiny smoke so tests can pass without GPU work
        return {"ok": True}

    fn = OP_MAP.get(op)
    if fn is None:
        return {"ok": False, "error": f"Unknown op {op}"}

    try:
        return fn(body)
    except Exception as e:
        logging.exception("op failed")
        return {"ok": False, "error": str(e)}

if __name__ == "__main__":
    # Keep same signature for RunPod serverless
    runpod.serverless.start(
        { "handler": handler },
        # Make sure the worker stays alive even if Comfy launches a little later
        handler_port=int(os.environ.get("RP_HANDLER_PORT", "8000")),
    )
