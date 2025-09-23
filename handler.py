import os
import time
import json
import uuid
import base64
import traceback
from io import BytesIO

import requests
from requests.adapters import HTTPAdapter, Retry

import runpod

# ----------------------------
# Config
# ----------------------------
COMFY_HOST = os.getenv("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.getenv("COMFY_PORT", "8188"))
COMFY_URL = f"http://{COMFY_HOST}:{COMFY_PORT}"

INPUT_DIR = os.getenv("INPUT_DIR", "/workspace/inputs")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/workspace/outputs")

# Path to your ComfyUI workflow JSON (faceswap pipeline)
# Make sure the file exists in the container at this path
WORKFLOW_PATH = os.getenv(
    "COMFY_WORKFLOW_PATH",
    "/workspace/ComfyUI/workflows/APIAutoFaceACE.json"
)

# How long we wait for ComfyUI to become ready in seconds
COMFY_WAIT_SECS = int(os.getenv("COMFY_WAIT_SECS", "45"))
COMFY_POLL_INTERVAL = 1.0  # seconds

# Optional S3 config (RunPod Network Volume over S3 API)
S3_ENDPOINT = os.getenv("S3_ENDPOINT")               # e.g. https://s3api-eu-ro-1.runpod.io
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_BUCKET = os.getenv("S3_BUCKET")
S3_REGION = os.getenv("S3_REGION", "auto")

USE_S3 = all([S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET])

# Requests session with retries (helps against flaky remote hosts/CDNs)
def _make_session():
    sess = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    sess.headers.update({"User-Agent": "runpod-comfy-faceswap/1.0"})
    return sess

HTTP = _make_session()


# ----------------------------
# Helpers
# ----------------------------
def _resp_ok(payload):
    return {"ok": True, **payload}

def _resp_err(msg, extra=None):
    out = {"ok": False, "error": msg}
    if extra:
        out.update(extra)
    return out

def _ensure_dirs():
    os.makedirs(INPUT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def _wait_for_comfy(timeout=COMFY_WAIT_SECS):
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            r = HTTP.get(f"{COMFY_URL}/system_stats", timeout=3)
            if r.ok:
                return r.json()
        except Exception as e:
            last_err = str(e)
        time.sleep(0.8)
    raise RuntimeError(f"ComfyUI did not come up in time: {last_err}")

def _download_image_to(path, url_or_b64):
    """
    Accepts either a data URL / raw base64 string, or an http(s) URL.
    Saves image bytes to `path`.
    Returns the basename (ComfyUI LoadImage expects name relative to INPUT_DIR).
    """
    # base64?
    if isinstance(url_or_b64, str) and (url_or_b64.startswith("data:") or len(url_or_b64) > 1000):
        # try parse data URL
        if url_or_b64.startswith("data:"):
            head, b64data = url_or_b64.split(",", 1)
        else:
            b64data = url_or_b64
        data = base64.b64decode(b64data)
        with open(path, "wb") as f:
            f.write(data)
        return os.path.basename(path)

    # http(s)
    r = HTTP.get(url_or_b64, timeout=15, stream=True)
    r.raise_for_status()
    content = r.content
    with open(path, "wb") as f:
        f.write(content)
    return os.path.basename(path)

def _load_workflow():
    if not os.path.exists(WORKFLOW_PATH):
        raise FileNotFoundError(f"Workflow not found at {WORKFLOW_PATH}")
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def _patch_workflow_images(workflow, source_name, target_name):
    """
    Replace any LoadImage node 'inputs.image' with our source/target basenames.
    Heuristic: The first LoadImage we encounter -> source, second -> target.
    If your workflow labels differ (e.g. 'source'/'target'), we still map 1st/2nd.
    """
    count = 0
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type", "").lower().startswith("loadimage"):
            if "inputs" in node and isinstance(node["inputs"], dict):
                if count == 0:
                    node["inputs"]["image"] = source_name
                elif count == 1:
                    node["inputs"]["image"] = target_name
                count += 1
    return workflow

def _queue_prompt(workflow):
    payload = {"prompt": workflow, "client_id": "runpod-worker"}
    r = HTTP.post(f"{COMFY_URL}/prompt", json=payload, timeout=20)
    r.raise_for_status()
    return r.json().get("prompt_id")

def _wait_prompt_done(prompt_id, timeout=600):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = HTTP.get(f"{COMFY_URL}/history/{prompt_id}", timeout=10)
        if r.ok:
            js = r.json()
            if prompt_id in js:
                last = js[prompt_id]
                status = last.get("status", {})
                if status.get("completed"):
                    return last
        time.sleep(1.0)
    raise TimeoutError("ComfyUI prompt did not complete in time")

def _collect_images_from_history(hist):
    """
    Collect SaveImage results: returns list of dicts with fields:
    - filename (relative to output dir)
    - subfolder
    - type (e.g. "output")
    - image_base64 (if image data present)
    """
    results = []
    if not hist:
        return results
    # Format per ComfyUI history schema
    for node_id, node_out in hist.get("outputs", {}).items():
        for img in node_out.get("images", []):
            entry = {
                "filename": img.get("filename"),
                "subfolder": img.get("subfolder"),
                "type": img.get("type"),
            }
            # Comfy may also return "data" as base64 for previews (not always present)
            if "data" in img:
                entry["image_base64"] = img["data"]
            results.append(entry)
    return results

def _upload_to_s3(local_path, key_name):
    """
    Upload local file to RunPod Network Volume (S3 compatible) and return a presigned URL.
    """
    import boto3
    from botocore.client import Config

    s3 = boto3.client(
        "s3",
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        endpoint_url=S3_ENDPOINT,
        region_name=S3_REGION,
        config=Config(s3={"addressing_style": "virtual"})
    )
    s3.upload_file(local_path, S3_BUCKET, key_name)
    # Return a 24h presigned URL
    url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": S3_BUCKET, "Key": key_name},
        ExpiresIn=86400
    )
    return url


# ----------------------------
# Ops
# ----------------------------
def op_ping(_):
    return _resp_ok({"pong": True, "ts": int(time.time())})

def op_health_check(_):
    stats = _wait_for_comfy()
    return _resp_ok({"stats": stats, "comfy_url": COMFY_URL})

def op_version(_):
    try:
        stats = _wait_for_comfy()
    except Exception:
        stats = None
    return _resp_ok({
        "worker": {
            "python": os.popen("python3 -V").read().strip(),
            "pid": os.getpid(),
        },
        "comfy": stats.get("system") if stats else None,
        "env": {
            "INPUT_DIR": INPUT_DIR,
            "OUTPUT_DIR": OUTPUT_DIR,
            "WORKFLOW_PATH": WORKFLOW_PATH
        }
    })

def op_faceswap(event):
    """
    Input accepts any of:
      - source_url + target_url  (preferred)
      - source_b64 + target_b64  (fallback)
    Optional:
      - return: "url" | "b64"   (default: "url" if S3 configured, else "b64")
    """
    _ensure_dirs()
    _wait_for_comfy()

    data = event or {}
    source = data.get("source_url") or data.get("source_b64")
    target = data.get("target_url") or data.get("target_b64")
    if not source or not target:
        return _resp_err("Provide 'source_url'/'source_b64' and 'target_url'/'target_b64'.")

    ret_pref = data.get("return")
    if ret_pref not in ("url", "b64", None):
        return _resp_err("Invalid 'return' value. Use 'url' or 'b64'.")
    if ret_pref is None:
        ret_pref = "url" if USE_S3 else "b64"

    job_id = str(uuid.uuid4())[:8]
    src_name = f"source_{job_id}.png"
    tgt_name = f"target_{job_id}.png"

    try:
        _download_image_to(os.path.join(INPUT_DIR, src_name), source)
        _download_image_to(os.path.join(INPUT_DIR, tgt_name), target)
    except Exception as e:
        return _resp_err(f"Failed to fetch inputs: {e}")

    # Build workflow
    try:
        wf = _load_workflow()
        wf = _patch_workflow_images(wf, src_name, tgt_name)
    except Exception as e:
        return _resp_err(f"Workflow error: {e}")

    # Queue + wait
    try:
        prompt_id = _queue_prompt(wf)
        hist = _wait_prompt_done(prompt_id, timeout=900)
        images = _collect_images_from_history(hist)
        if not images:
            return _resp_err("No images produced by workflow.")
    except Exception as e:
        return _resp_err(f"Pipeline failed: {e}", {"trace": traceback.format_exc()})

    # Use the first output image
    first = images[0]
    # Resolve disk file location created by SaveImage node
    filename = first.get("filename")
    subfolder = first.get("subfolder", "")
    # Comfy by default saves under OUTPUT_DIR / {subfolder} / {filename}
    disk_path = os.path.join(OUTPUT_DIR, subfolder, filename) if filename else None

    # Return according to preference
    if ret_pref == "url" and USE_S3:
        if not disk_path or not os.path.exists(disk_path):
            return _resp_err("Output file not found on disk to upload.")
        key = f"faceswap/{time.strftime('%Y%m%d')}/{job_id}_{filename}"
        try:
            url = _upload_to_s3(disk_path, key)
            return _resp_ok({
                "result_url": url,
                "file": {"name": filename, "subfolder": subfolder}
            })
        except Exception as e:
            # Fallback to b64 if upload fails
            with open(disk_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            return _resp_ok({
                "result_b64": b64,
                "note": f"S3 upload failed: {e}"
            })

    # Return b64 (either by choice or no S3)
    # Prefer reading disk; if not available but history has base64, use that.
    if disk_path and os.path.exists(disk_path):
        with open(disk_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
        return _resp_ok({"result_b64": img_b64})
    elif first.get("image_base64"):
        return _resp_ok({"result_b64": first["image_base64"]})
    else:
        return _resp_err("Output image bytes unavailable.")

# ----------------------------
# Dispatcher
# ----------------------------
OPS = {
    "ping": op_ping,
    "health_check": op_health_check,
    "version": op_version,
    "faceswap": op_faceswap
}

def handler(event):
    try:
        body = event.get("input") if isinstance(event, dict) else None
        if not isinstance(body, dict):
            return _resp_err("Invalid request body.")
        op = body.get("op")
        if not op:
            return _resp_err("Missing 'op'.")
        func = OPS.get(op)
        if not func:
            return _resp_err(f"Unknown op '{op}'")
        payload = {k: v for k, v in body.items() if k != "op"}
        return func(payload)
    except Exception as e:
        return _resp_err(f"Unhandled error: {e}", {"trace": traceback.format_exc()})

runpod.serverless.start({"handler": handler})
