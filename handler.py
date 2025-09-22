import os
import base64
import time
import json
import urllib.request
from typing import Dict, Any

import runpod

COMFY_HOST = os.getenv("COMFY_BIND_HOST", "127.0.0.1")
COMFY_PORT = int(os.getenv("COMFY_BIND_PORT", "8188"))
COMFY_URL = f"http://{COMFY_HOST}:{COMFY_PORT}"

def _ping(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            r.read()
        return True
    except Exception:
        return False

def _ensure_comfy_ready(max_wait_s: int = 30) -> bool:
    """Light readiness gate used by ops that need Comfy."""
    for _ in range(max_wait_s):
        if _ping(f"{COMFY_URL}/system_stats"):
            return True
        time.sleep(1)
    return False

def _download_to_b64(u: str) -> str:
    with urllib.request.urlopen(u, timeout=15) as r:
        data = r.read()
    return base64.b64encode(data).decode("utf-8")

def _ok(**kw) -> Dict[str, Any]:
    o = {"ok": True}
    o.update(kw)
    return o

def _fail(msg: str, **kw) -> Dict[str, Any]:
    o = {"ok": False, "error": msg}
    o.update(kw)
    return o

def handler(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Supported ops:
      - health_check: quick pass/fail used by tests.json
      - comfy_ping: verify ComfyUI is up (optional)
      - faceswap:  source_url + target_url -> (placeholder) echo result
    """
    req = event.get("input") or {}
    op = req.get("op", "health_check")

    # Minimal/fast test op (decoupled from repo assets)
    if op == "health_check":
        return _ok(message="pong", comfy_url=COMFY_URL)

    if op == "comfy_ping":
        if _ensure_comfy_ready(30):
            return _ok(comfy_url=COMFY_URL)
        return _fail("ComfyUI not ready", comfy_url=COMFY_URL)

    if op == "faceswap":
        # This stub demonstrates receiving URLs and returning a URL.
        # Wire to your Comfy workflow here once you’re ready.
        source_url = req.get("source_url")
        target_url = req.get("target_url")
        if not source_url or not target_url:
            return _fail("Provide source_url and target_url")

        # Make sure ComfyUI is listening before we try to use it.
        if not _ensure_comfy_ready(60):
            return _fail("ComfyUI did not come up in time", comfy_url=COMFY_URL)

        # Demo: validate we can fetch both images
        try:
            _ = _download_to_b64(source_url)
            _ = _download_to_b64(target_url)
        except Exception as e:
            return _fail(f"Failed to fetch input images: {e}")

        # TODO: Replace with your actual Comfy graph submission + polling
        # For now, return source_url to prove round-trip works.
        return _ok(result_url=source_url, comfy_url=COMFY_URL)

    return _fail(f"Unknown op '{op}'")

runpod.serverless.start({"handler": handler})
