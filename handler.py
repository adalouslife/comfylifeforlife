# handler.py
import os
import time
import base64
import asyncio
import aiohttp
import runpod
from fastapi import FastAPI
from pydantic import BaseModel

COMFY_PORT = int(os.getenv("COMFY_PORT", "8188"))
COMFY_URL = f"http://127.0.0.1:{COMFY_PORT}"

# ---------- FastAPI app for uvicorn (start.sh) ----------
app = FastAPI(title="Comfy Faceswap Worker")

class RequestModel(BaseModel):
    op: str
    source_url: str | None = None
    target_url: str | None = None

async def _http_ok(url: str) -> bool:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                return r.status == 200
    except Exception:
        return False

async def wait_for_comfy(timeout_s: int = 120) -> None:
    start = time.time()
    while time.time() - start < timeout_s:
        if await _http_ok(f"{COMFY_URL}/system_stats"):
            return
        await asyncio.sleep(1)
    raise RuntimeError("ComfyUI did not come up in time.")

@app.get("/health")
async def health():
    ok = await _http_ok(f"{COMFY_URL}/system_stats")
    return {"ok": ok, "comfy_url": COMFY_URL}

# ---------- RunPod job handler ----------
def run_job(event):
    """RunPod entrypoint: expects event['input'] with 'op'."""
    inp = event.get("input") or {}
    op = inp.get("op", "health_check")

    if op == "health_check":
        return {"ok": True, "comfy_url": COMFY_URL}

    if op == "faceswap":
        src = inp.get("source_url")
        tgt = inp.get("target_url")
        if not src or not tgt:
            raise ValueError("Provide 'source_url' and 'target_url'.")

        # Ensure ComfyUI up
        loop = asyncio.get_event_loop()
        loop.run_until_complete(wait_for_comfy(120))

        # Just validate the URLs resolve before you wire the workflow
        ok_src = loop.run_until_complete(_http_ok(src))
        ok_tgt = loop.run_until_complete(_http_ok(tgt))
        if not ok_src or not ok_tgt:
            raise ValueError("source_url or target_url is not reachable.")

        # TODO: Call your ComfyUI workflow here (POST /prompt with your graph)
        # and then parse the result and return a URL to the output (S3/volume).
        # For now we return a stub so the op is recognized and the pipeline runs.
        return {
            "ok": True,
            "message": "faceswap op stub executed (wire your workflow next).",
            "comfy_url": COMFY_URL
        }

    raise ValueError(f"Unknown op '{op}'.")

# Wire for RunPod serverless
runpod.serverless.start(
    {"handler": run_job}
)
