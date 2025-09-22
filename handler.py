import os, io, sys, json, time, uuid, base64, shutil, random, string, logging, pathlib, requests, runpod, subprocess
from typing import Dict, Any, List, Tuple, Optional

# ---------- Config ----------
COMFY_HOST = os.getenv("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.getenv("COMFY_PORT", "8188"))
COMFY_URL = f"http://{COMFY_HOST}:{COMFY_PORT}"

WORKDIR = pathlib.Path("/workspace")
COMFY_DIR = WORKDIR / "ComfyUI"
INPUT_DIR = COMFY_DIR / "input"
OUTPUT_DIR = COMFY_DIR / "output"
WORKFLOW_PATH = WORKDIR / "comfyui" / "workflows" / "APIAutoFaceACE.json"

STORAGE_DIR = pathlib.Path(os.getenv("STORAGE_DIR", "/runpod-volume"))

# Node IDs inside APIAutoFaceACE.json (your note)
FACE_NODE_ID = "240"  # Load New Face
BASE_NODE_ID = "420"  # Load Image

COMFY_STARTED = False

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("handler")

# ---------- Small helpers ----------
def _rand_token(n=8) -> str:
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=n))

def _sleep_ms(ms: int):
    time.sleep(ms / 1000.0)

def _http_get(url: str, **kw) -> requests.Response:
    r = requests.get(url, timeout=kw.pop("timeout", 30), **kw)
    r.raise_for_status()
    return r

def _http_post(url: str, json=None, data=None, files=None, **kw) -> requests.Response:
    r = requests.post(url, json=json, data=data, files=files, timeout=kw.pop("timeout", 60), **kw)
    r.raise_for_status()
    return r

# ---------- Catbox upload ----------
def _upload_to_catbox(binary: bytes, filename: str) -> str:
    files = {'fileToUpload': (filename, io.BytesIO(binary), 'application/octet-stream')}
    data = {'reqtype': 'fileupload'}
    r = _http_post("https://catbox.moe/user/api.php", data=data, files=files, timeout=120)
    url = r.text.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"Catbox upload failed: {url}")
    return url

# ---------- Comfy lifecycle ----------
def _start_comfy_once():
    global COMFY_STARTED
    if COMFY_STARTED:
        return

    # Make sure input/output exist (and symlink into STORAGE_DIR if present)
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if STORAGE_DIR.exists():
        (STORAGE_DIR / "inputs").mkdir(parents=True, exist_ok=True)
        (STORAGE_DIR / "outputs").mkdir(parents=True, exist_ok=True)
        # Symlink if not already
        for src, dest in [(STORAGE_DIR / "inputs", INPUT_DIR), (STORAGE_DIR / "outputs", OUTPUT_DIR)]:
            try:
                if dest.is_symlink() or dest.exists():
                    # If it's a real directory, replace with symlink
                    if dest.is_dir() and not dest.is_symlink():
                        shutil.rmtree(dest)
                if not dest.exists():
                    dest.symlink_to(src, target_is_directory=True)
            except Exception as e:
                log.warning(f"Symlink setup skipped: {e}")

    env = os.environ.copy()
    cmd = [
        sys.executable, "-u", str(COMFY_DIR / "main.py"),
        "--listen", COMFY_HOST,
        "--port", str(COMFY_PORT),
        "--disable-auto-launch",
        "--highvram",
        "--cuda-device", "0",
    ]
    subprocess.Popen(cmd, cwd=str(COMFY_DIR), env=env)
    log.info(f"Started ComfyUI at {COMFY_URL}")

    # Wait for server to be ready
    deadline = time.time() + 180  # give it up to 3 minutes on cold start
    last_err = None
    while time.time() < deadline:
        try:
            _http_get(f"{COMFY_URL}/system_stats", timeout=3)
            COMFY_STARTED = True
            log.info("ComfyUI is ready.")
            return
        except Exception as e:
            last_err = e
            _sleep_ms(500)
    raise RuntimeError(f"ComfyUI did not come up in time: {last_err}")

# ---------- Comfy API ----------
def _queue_prompt(prompt_map: Dict[str, Any], client_id: Optional[str] = None) -> str:
    payload = {
        "client_id": client_id or f"runpod-{_rand_token()}",
        "prompt": prompt_map
    }
    r = _http_post(f"{COMFY_URL}/prompt", json=payload, timeout=120)
    data = r.json()
    if "prompt_id" not in data:
        raise RuntimeError(f"Bad /prompt response: {data}")
    return data["prompt_id"]

def _wait_history(prompt_id: str, timeout_s: int = 600) -> Dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        r = _http_get(f"{COMFY_URL}/history/{prompt_id}", timeout=20)
        js = r.json()
        if prompt_id in js:
            item = js[prompt_id]
            status = item.get("status", {}).get("status", "")
            if status in ("completed", "error"):
                return item
        _sleep_ms(500)
    raise RuntimeError("Timeout waiting for prompt history.")

def _fetch_output_images(history_item: Dict[str, Any]) -> List[Tuple[bytes, str]]:
    """Return list of (binary, filename) from history outputs."""
    results: List[Tuple[bytes, str]] = []
    for node_id, node_out in history_item.get("outputs", {}).items():
        for img in node_out.get("images", []):
            filename = img.get("filename")
            subfolder = img.get("subfolder", "")
            view_url = f"{COMFY_URL}/view?filename={filename}&subfolder={subfolder}&type=output"
            resp = _http_get(view_url, timeout=60)
            results.append((resp.content, filename))
    return results

# ---------- Files ----------
def _download_to_inputs(url: str, dest_name: Optional[str] = None) -> str:
    """Download url into ComfyUI/input and return the filename (not full path) for LoadImage node."""
    dest_name = dest_name or (uuid.uuid4().hex + os.path.splitext(url.split("?")[0])[-1])
    if "." not in dest_name:
        dest_name += ".jpg"
    dest_path = INPUT_DIR / dest_name
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(r.raw, f)
    return dest_name

def _load_workflow(path: pathlib.Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _patch_images_in_prompt(prompt_map: Dict[str, Any], face_filename: str, base_filename: str):
    """
    Replace image filenames in two LoadImage nodes.
    Prefers node IDs FACE_NODE_ID and BASE_NODE_ID; if missing, falls back to selecting the first two LoadImage nodes found.
    """
    def is_load_image(node_def: Dict[str, Any]) -> bool:
        return node_def.get("class_type") == "LoadImage"
    def set_image(node_def: Dict[str, Any], filename: str):
        node_def.setdefault("inputs", {})["image"] = filename

    if FACE_NODE_ID in prompt_map and is_load_image(prompt_map[FACE_NODE_ID]):
        set_image(prompt_map[FACE_NODE_ID], face_filename)
    if BASE_NODE_ID in prompt_map and is_load_image(prompt_map[BASE_NODE_ID]):
        set_image(prompt_map[BASE_NODE_ID], base_filename)

    # Fallback if IDs changed in the workflow
    missing = []
    if not (FACE_NODE_ID in prompt_map and is_load_image(prompt_map.get(FACE_NODE_ID, {}))):
        missing.append("face")
    if not (BASE_NODE_ID in prompt_map and is_load_image(prompt_map.get(BASE_NODE_ID, {}))):
        missing.append("base")

    if missing:
        # Find first two LoadImage nodes
        load_nodes = [k for k, v in prompt_map.items() if is_load_image(v)]
        if len(load_nodes) < 2:
            raise RuntimeError("Workflow does not contain two LoadImage nodes as expected.")
        # Assign deterministically
        set_image(prompt_map[load_nodes[0]], face_filename)
        set_image(prompt_map[load_nodes[1]], base_filename)

# ---------- Ops ----------
def _op_health_check(_: Dict[str, Any]) -> Dict[str, Any]:
    _start_comfy_once()
    _http_get(f"{COMFY_URL}/system_stats", timeout=5)
    return {"ok": True, "comfy_url": COMFY_URL}

def _op_comfy_passthrough(inp: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accepts a raw payload exactly as Comfy expects:
      input.payload = { "prompt": {...}, "client_id": "..." }
    """
    _start_comfy_once()
    payload = inp.get("payload")
    if not isinstance(payload, dict) or "prompt" not in payload:
        raise ValueError("Provide 'payload' with a Comfy 'prompt' mapping.")
    prompt_id = _queue_prompt(payload["prompt"], client_id=payload.get("client_id"))
    hist = _wait_history(prompt_id)
    images = _fetch_output_images(hist)
    urls = [_upload_to_catbox(b, fname) for (b, fname) in images]
    return {"ok": True, "prompt_id": prompt_id, "images": urls}

def _op_swap(inp: Dict[str, Any]) -> Dict[str, Any]:
    """
    Face swap using comfyui/workflows/APIAutoFaceACE.json

    input:
      - source_url: face image URL (the face to insert)
      - target_url: base image URL

    returns: { images: [catbox URLs...] }
    """
    _start_comfy_once()
    source_url = inp.get("source_url") or inp.get("source")
    target_url = inp.get("target_url") or inp.get("target")
    if not source_url or not target_url:
        raise ValueError("Provide 'source_url' and 'target_url'.")

    face_name = _download_to_inputs(source_url, dest_name=f"face_{_rand_token()}.jpg")
    base_name = _download_to_inputs(target_url, dest_name=f"base_{_rand_token()}.jpg")

    if not WORKFLOW_PATH.exists():
        raise RuntimeError(f"Workflow not found at {WORKFLOW_PATH}")

    prompt_map = _load_workflow(WORKFLOW_PATH)
    _patch_images_in_prompt(prompt_map, face_name, base_name)

    prompt_id = _queue_prompt(prompt_map)
    hist = _wait_history(prompt_id)
    status = hist.get("status", {}).get("status", "")
    if status == "error":
        return {"ok": False, "prompt_id": prompt_id, "error": hist.get("status", {}).get("error", "Unknown")}

    images = _fetch_output_images(hist)
    if not images:
        return {"ok": False, "prompt_id": prompt_id, "error": "No images produced by workflow."}

    urls = [_upload_to_catbox(b, fname) for (b, fname) in images]
    return {"ok": True, "prompt_id": prompt_id, "images": urls}

# ---------- RunPod Handler ----------
def handler(event):
    """
    input.op:
      - "health_check"
      - "swap" (alias: "faceswap")
      - "comfy_passthrough"
    """
    try:
        inp = event.get("input") or {}
        op = (inp.get("op") or "health_check").lower().strip()

        if op in ("health", "health_check", "ping"):
            return _op_health_check(inp)
        if op in ("swap", "faceswap"):
            return _op_swap(inp)
        if op in ("comfy_passthrough", "passthrough"):
            return _op_comfy_passthrough(inp)

        return {"ok": False, "error": f"Unknown op '{op}'."}

    except requests.HTTPError as he:
        return {"ok": False, "error": f"HTTPError: {he}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

runpod.serverless.start({"handler": handler})
