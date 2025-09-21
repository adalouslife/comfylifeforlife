import os, time, json, base64, tempfile, requests, shutil, threading, subprocess
import runpod

COMFY_HOST = os.getenv("COMFYUI_HOST", "127.0.0.1")
COMFY_PORT = int(os.getenv("COMFYUI_PORT", "8188"))
COMFY_URL  = f"http://{COMFY_HOST}:{COMFY_PORT}"

# Directories — adjust if your ComfyUI lives elsewhere
COMFY_ROOT        = os.getenv("COMFYUI_ROOT", "/workspace/ComfyUI")
COMFY_INPUT_DIR   = os.getenv("COMFYUI_INPUT_DIR", f"{COMFY_ROOT}/input")
COMFY_OUTPUT_DIR  = os.getenv("COMFYUI_OUTPUT_DIR", f"{COMFY_ROOT}/output")
WORKFLOW_FILE     = os.getenv("WORKFLOW_FILE", "/app/workflows/faceswap_api.json")

# Optional Network Volume for models/cache (default RunPod mount)
NV_MOUNT = os.getenv("VOLUME_MOUNT", "/runpod-volume")
USE_NV   = os.getenv("USE_NETWORK_VOLUME", "true").lower() == "true"
NV_MODELS_PATH = os.getenv("NV_MODELS_PATH", f"{NV_MOUNT}/models/ComfyUI")  # your shared models
COMFY_MODELS_PATH = os.getenv("COMFYUI_MODELS_PATH", f"{COMFY_ROOT}/models")

TEST_MODE = os.getenv("RUNPOD_TEST_MODE", "false").lower() == "true"

def _symlink_models_once():
    if not USE_NV:
        return
    os.makedirs(NV_MODELS_PATH, exist_ok=True)
    os.makedirs(os.path.dirname(COMFY_MODELS_PATH), exist_ok=True)
    # Replace models dir with symlink to the Network Volume
    if os.path.islink(COMFY_MODELS_PATH):
        return
    if os.path.exists(COMFY_MODELS_PATH):
        # move any pre-downloaded models to NV to preserve them
        tmp = f"{NV_MODELS_PATH}/_import_once"
        os.makedirs(tmp, exist_ok=True)
        for name in os.listdir(COMFY_MODELS_PATH):
            src = os.path.join(COMFY_MODELS_PATH, name)
            dst = os.path.join(tmp, name)
            if not os.path.exists(dst):
                shutil.move(src, dst)
        shutil.rmtree(COMFY_MODELS_PATH)
    os.symlink(NV_MODELS_PATH, COMFY_MODELS_PATH)

def _start_comfy_background():
    _symlink_models_once()
    # Ensure input/output exist
    os.makedirs(COMFY_INPUT_DIR, exist_ok=True)
    os.makedirs(COMFY_OUTPUT_DIR, exist_ok=True)

    # Launch ComfyUI headless in background
    # If your ComfyUI path differs, adjust python entry and args.
    cmd = [
        "python", f"{COMFY_ROOT}/main.py",
        "--listen", "127.0.0.1",
        "--port", str(COMFY_PORT),
        "--disable-auto-launch"
    ]
    # You can add: "--force-fp16", "--lowvram", etc. from env if needed
    extra = os.getenv("COMFY_EXTRA_ARGS", "")
    if extra.strip():
        cmd += extra.split()

    def run():
        subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    threading.Thread(target=run, daemon=True).start()

def _wait_for_comfy(timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(f"{COMFY_URL}/system_stats", timeout=2)
            if r.ok:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False

def _load_workflow():
    with open(WORKFLOW_FILE, "r", encoding="utf-8") as f:
        return json.load(f)  # must be the dict of nodes keyed by string ids

def _save_input_image_from_base64(b64_str, filename):
    data = base64.b64decode(b64_str)
    with open(os.path.join(COMFY_INPUT_DIR, filename), "wb") as f:
        f.write(data)

def _save_input_image_from_url(url, filename):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    with open(os.path.join(COMFY_INPUT_DIR, filename), "wb") as f:
        f.write(r.content)

def _submit_prompt(prompt_graph):
    r = requests.post(f"{COMFY_URL}/prompt", json={"prompt": prompt_graph}, timeout=30)
    r.raise_for_status()
    return r.json()["prompt_id"]

def _wait_for_history(prompt_id, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = requests.get(f"{COMFY_URL}/history/{prompt_id}", timeout=10)
        if r.ok:
            j = r.json()
            if prompt_id in j:
                return j[prompt_id]  # full history dict
        time.sleep(1.5)
    raise TimeoutError("ComfyUI job timed out")

def _extract_output_images(history_obj):
    """Returns list of dicts: [{filename, subfolder, type}]"""
    # ComfyUI history structure varies by nodes; we scan all nodes
    outputs = []
    for node_id, node_data in history_obj.get("outputs", {}).items():
        for img in node_data.get("images", []):
            if img.get("type") == "output":
                outputs.append({"filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img["type"]})
    return outputs

def _fetch_image_b64(filename, subfolder=""):
    params = {"filename": filename}
    if subfolder:
        params["subfolder"] = subfolder
    # /view serves the image file
    r = requests.get(f"{COMFY_URL}/view", params=params, timeout=30)
    r.raise_for_status()
    return base64.b64encode(r.content).decode("ascii")

# ---------------- RunPod handler ----------------

def handler(event):
    """
    Expected inputs for real runs (examples):
    {
      "mode": "faceswap",
      "source_image_b64": "...",
      "target_image_b64": "..."
      # OR:
      "source_image_url": "https://.../src.png",
      "target_image_url": "https://.../dst.png"
    }

    For smoke tests:
    { "op": "health_check" }
    """
    # 1) fast path for tests
    if TEST_MODE and (event.get("op") == "health_check" or event.get("mode") == "smoke"):
        return {"ok": True, "message": "Test mode passed ✅"}

    # 2) ensure ComfyUI is up
    if not _wait_for_comfy(timeout=180):
        return {"ok": False, "error": "ComfyUI did not become ready in time."}

    # 3) ingest images
    source_name = "source.png"
    target_name = "target.png"

    try:
        if "source_image_b64" in event:
            _save_input_image_from_base64(event["source_image_b64"], source_name)
        elif "source_image_url" in event:
            _save_input_image_from_url(event["source_image_url"], source_name)
        else:
            return {"ok": False, "error": "Missing source image (base64 or url)."}

        if "target_image_b64" in event:
            _save_input_image_from_base64(event["target_image_b64"], target_name)
        elif "target_image_url" in event:
            _save_input_image_from_url(event["target_image_url"], target_name)
        else:
            return {"ok": False, "error": "Missing target image (base64 or url)."}
    except Exception as e:
        return {"ok": False, "error": f"Failed to load inputs: {e}"}

    # 4) load workflow template and submit
    try:
        graph = _load_workflow()
        # IMPORTANT: your workflow must contain two LoadImage nodes with
        # inputs.image == "source.png" and "target.png".
        # If you prefer dynamic injection, you can edit the graph here.
        prompt_id = _submit_prompt(graph)
        history = _wait_for_history(prompt_id)
        outs = _extract_output_images(history)
        if not outs:
            return {"ok": False, "error": "No output images found in history."}
        # return first output (or all)
        images_b64 = []
        for meta in outs:
            b64 = _fetch_image_b64(meta["filename"], meta.get("subfolder", ""))
            images_b64.append({
                "filename": meta["filename"],
                "image_base64": b64
            })
        return {"ok": True, "images": images_b64}
    except requests.HTTPError as e:
        # Helpful error surface if /prompt rejects the payload
        try:
            detail = e.response.text[:400]
        except Exception:
            detail = str(e)
        return {"ok": False, "error": f"ComfyUI HTTPError: {e}", "detail": detail}
    except Exception as e:
        return {"ok": False, "error": f"Unhandled: {e}"}

def _bootstrap():
    _start_comfy_background()

# Start RunPod serverless with on_start so ComfyUI launches on cold start
runpod.serverless.start({"handler": handler, "on_start": _bootstrap})
