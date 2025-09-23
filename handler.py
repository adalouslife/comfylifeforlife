import os, json, time, base64, subprocess, uuid, glob, shlex
from pathlib import Path
from typing import Dict, Any, Optional

import requests
import runpod

COMFY_HOST = os.getenv("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.getenv("COMFY_PORT", "8188"))
COMFY_URL  = f"http://{COMFY_HOST}:{COMFY_PORT}"

INPUT_DIR   = Path(os.getenv("INPUT_DIR", "/workspace/ComfyUI/input"))
OUTPUT_DIR  = Path(os.getenv("OUTPUT_DIR", "/workspace/ComfyUI/output"))
WORKFLOW_PATH = Path(os.getenv("WORKFLOW_PATH", "/workspace/comfyui/workflows/APIAutoFaceACE.json"))

INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

UA = "curl/8.6 (RunPod Comfy Worker)"

def log(msg: str, rid: Optional[str] = None):
    if rid:
        print(json.dumps({"requestId": rid, "message": msg, "level": "INFO"}), flush=True)
    else:
        print(json.dumps({"requestId": None, "message": msg, "level": "INFO"}), flush=True)

def curl_download(url: str, out_path: Path, timeout: int = 60) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "curl", "-fsSL",
        "--retry", "5", "--retry-all-errors",
        "--connect-timeout", "10",
        "--max-time", str(timeout),
        "-A", UA,
        "-o", str(out_path),
        url
    ]
    subprocess.run(cmd, check=True)

def fetch_file(url: str, fname: str, rid: Optional[str] = None) -> Path:
    dest = INPUT_DIR / fname
    try:
        curl_download(url, dest)
        return dest
    except subprocess.CalledProcessError as e:
        log(f"[download] curl failed: {e}. Trying requests.", rid)
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": UA})
            r.raise_for_status()
            dest.write_bytes(r.content)
            return dest
        except Exception as e2:
            raise RuntimeError(f"Failed to fetch {url}: {e2}")

def comfy_get(path: str):
    return requests.get(f"{COMFY_URL}{path}", timeout=10)

def comfy_post_json(path: str, payload: Dict[str, Any]):
    return requests.post(f"{COMFY_URL}{path}", json=payload, timeout=60)

def wait_for_comfy(rid: Optional[str] = None):
    for i in range(60):
        try:
            if comfy_get("/system_stats").ok:
                return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("ComfyUI did not come up in time.")

def handle_ping(_: Dict[str, Any], rid: Optional[str]) -> Dict[str, Any]:
    return {"ok": True, "message": "pong"}

def handle_health(_: Dict[str, Any], rid: Optional[str]) -> Dict[str, Any]:
    try:
        wait_for_comfy(rid)
        return {"ok": True, "comfy_url": COMFY_URL}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def submit_workflow_with_images(source_path: Path, target_path: Path, rid: Optional[str]) -> Path:
    # Load your saved workflow and inject file node values.
    # Assumes your workflow uses two LoadImage nodes named e.g. "Source" and "Target".
    graph = json.loads(WORKFLOW_PATH.read_text())
    # You MUST adjust node ids/keys below to match your actual workflow.
    # Example: find nodes with "inputs": {"image": "..."} and replace with file names.
    def assign_image(node_label_contains: str, file_name: str) -> None:
        for k, node in graph.items():
            label = node.get("_meta", {}).get("title", "") or node.get("class_type", "")
            if node_label_contains.lower() in str(label).lower():
                if "inputs" in node and "image" in node["inputs"]:
                    node["inputs"]["image"] = file_name

    assign_image("source", source_path.name)
    assign_image("target", target_path.name)

    # Send prompt
    prompt_id = str(uuid.uuid4())
    resp = comfy_post_json("/prompt", {"prompt": graph, "client_id": prompt_id})
    resp.raise_for_status()

    # Poll history for images.
    # Simpler: watch OUTPUT_DIR and pick the newest created file after we submitted.
    deadline = time.time() + 300
    last_mtime = 0.0
    candidate: Optional[Path] = None
    while time.time() < deadline:
        files = sorted(OUTPUT_DIR.glob("**/*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            newest = files[0]
            if newest.stat().st_mtime > last_mtime:
                candidate = newest
                last_mtime = newest.stat().st_mtime
        # Break as soon as we see a new file appear and it has a stable size (simple settle check)
        if candidate and candidate.exists():
            size1 = candidate.stat().st_size
            time.sleep(0.8)
            size2 = candidate.stat().st_size
            if size2 > 0 and size2 == size1:
                return candidate
        time.sleep(1.2)

    raise RuntimeError("Timeout waiting for output image from workflow.")

def upload_to_catbox(file_path: Path) -> Optional[str]:
    # Anonymous file upload to get a public URL quickly.
    # POST https://catbox.moe/user/api.php  (reqtype=fileupload, fileToUpload=@file)
    try:
        cmd = f'curl -fsSL -A {shlex.quote(UA)} -F "reqtype=fileupload" -F "fileToUpload=@{file_path}" https://catbox.moe/user/api.php'
        out = subprocess.check_output(cmd, shell=True, text=True, timeout=60)
        url = out.strip()
        if url.startswith("http"):
            return url
    except Exception:
        pass
    return None

def handle_faceswap(event: Dict[str, Any], rid: Optional[str]) -> Dict[str, Any]:
    """
    input:
    {
      "op": "faceswap",
      "source_url": "<url>",
      "target_url": "<url>"
    }
    """
    source_url = event.get("source_url")
    target_url = event.get("target_url")
    if not source_url or not target_url:
        return {"ok": False, "error": "Provide 'source_url' and 'target_url'."}

    wait_for_comfy(rid)

    sid = uuid.uuid4().hex[:8]
    source_path = fetch_file(source_url, f"source_{sid}.jpg", rid)
    target_path = fetch_file(target_url, f"target_{sid}.jpg", rid)
    log(f"[faceswap] Downloaded inputs: {source_path.name}, {target_path.name}", rid)

    try:
        out_path = submit_workflow_with_images(source_path, target_path, rid)
    except Exception as e:
        return {"ok": False, "error": f"Workflow failed: {e}"}

    public_url = upload_to_catbox(out_path)

    return {
        "ok": True,
        "result_path": str(out_path),
        "result_url": public_url,   # may be None if upload failed
        "comfy_url": COMFY_URL
    }

def handler(event: Dict[str, Any]) -> Dict[str, Any]:
    rid = event.get("id") or event.get("request_id")
    op = (event.get("op") or event.get("operation") or event.get("mode") or "").lower().strip()

    if op in ("ping", "noop"):
        return handle_ping(event, rid)
    if op in ("health", "health_check", "status"):
        return handle_health(event, rid)
    if op == "faceswap":
        return handle_faceswap(event, rid)

    return {"ok": False, "error": f"Unknown op '{op}'."}

if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
