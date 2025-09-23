import runpod
import requests

COMFY_URL = "http://127.0.0.1:8188"

def handler(job):
    inp = job["input"]
    op = inp.get("op", "ping")

    if op == "ping":
        return {"ok": True}
    elif op == "health_check":
        try:
            r = requests.get(f"{COMFY_URL}/system_stats", timeout=5)
            return {"ok": True, "stats": r.json()}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    else:
        return {"ok": False, "error": f"Unknown op {op}"}

runpod.serverless.start({"handler": handler})
