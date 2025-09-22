import os
import time
import json
import base64
import traceback
from typing import Any, Dict, Optional

import runpod
import requests

COMFY_HOST = os.getenv("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.getenv("COMFY_PORT", "8188"))
COMFY_URL = f"http://{COMFY_HOST}:{COMFY_PORT}"

# ---------- Utilities ----------

def _ok(**kwargs):
    out = {"ok": True}
    out.update(kwargs)
    return out

def _err(msg: str, **kwargs):
    out = {"ok": False, "error": msg}
    out.update(kwargs)
    return out

def comfy_ready(timeout_s: int = 3) -> bool:
    """Lightweight readiness probe used by health_check."""
    try:
        r = requests.get(f"{COMFY_URL}/system_stats", timeout=timeout_s)
        return r.status_code == 200
    except Exception:
        return False

def fetch_image_bytes(url: str, timeout: int = 30) -> bytes:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content

# ---------- Handlers ----------

def handle_health_check(_: Dict[str, Any]) -> Dict[str, Any]:
    return _ok(comfy_url=COMFY_URL, comfy_up=comfy_ready())

def handle_faceswap(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Minimal stub that validates inputs and returns them back.
    Wire your Comfy workflow here later. For now, it never blocks tests.
    """
    source_url = payload.get("source_url")
    target_url = payload.get("target_url")
    if not source_url or not target_url:
        return _err("Provide 'source_url' and 'target_url'.")

    # Optional: ensure URLs are reachable (soft fail -> better error)
    try:
        _ = requests.head(source_url, timeout=5)
        _ = requests.head(target_url, timeout=5)
    except Exception:
        # Don't fail the job just because HEAD is blocked; we’ll accept.
        pass

    # TODO: Replace with actual Comfy graph submission + polling.
    # For now, echo the inputs to prove the endpoint is working.
    return _ok(
        comfy_url=COMFY_URL,
        message="faceswap stub executed. Wire your Comfy workflow next.",
        source_url=source_url,
        target_url=target_url
    )

# ---------- Router ----------

def handler(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    RunPod entry. Accepted inputs:
      { "op": "health_check" }
      { "op": "faceswap", "source_url": "...", "target_url": "..." }
    """
    try:
        payload = event.get("input") or {}
        op = (payload.get("op") or "").lower().strip()

        if not op:
            # Default to health for safety in tests
            return handle_health_check(payload)

        if op == "health_check":
            return handle_health_check(payload)

        if op == "faceswap":
            # Ensure Comfy is up before we pretend to run workflow
            if not comfy_ready():
                return _err("ComfyUI not ready.")
            return handle_faceswap(payload)

        return _err(f"Unknown op '{op}'. Supported: health_check, faceswap")
    except Exception as e:
        return _err("Unhandled exception", detail=str(e), trace=traceback.format_exc())

# Local debug (optional)
if __name__ == "__main__":
    print(json.dumps(handler({"input": {"op": "health_check"}}), indent=2))
