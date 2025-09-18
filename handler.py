#!/usr/bin/env python3
import base64, io, json, os, time, typing as T
from dataclasses import dataclass
import requests, runpod

# ----- Config -----
COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
WORKFLOW_PATH = "/workspace/comfyui/workflows/APIAutoFaceACE.json"
UPLOAD_ENDPOINT  = f"{COMFY_URL}/upload/image"
PROMPT_ENDPOINT  = f"{COMFY_URL}/prompt"
HISTORY_ENDPOINT = f"{COMFY_URL}/history"
VIEW_ENDPOINT    = f"{COMFY_URL}/view"
HTTP_TIMEOUT = (5, 60)
CLIENT_ID = "runpod-worker"

def _b64_to_bytes(s: str) -> bytes:
    s = s.strip()
    if s.startswith("data:"):
        s = s.split(",", 1)[1]
    return base64.b64decode(s)

def _get_bytes_from_url(url: str) -> bytes:
    r = requests.get(url, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.content

def _upload(name_hint: str, data: bytes) -> str:
    files = {"image": (name_hint, io.BytesIO(data), "application/octet-stream")}
    r = requests.post(UPLOAD_ENDPOINT, files=files, timeout=HTTP_TIMEOUT)
    try:
        r.raise_for_status()
        js = r.json()
        if isinstance(js, dict) and "name" in js:
            return js["name"]
        if isinstance(js, list) and js and isinstance(js[0], dict) and "name" in js[0]:
            return js[0]["name"]
        if isinstance(js, str):
            return js.strip()
        raise RuntimeError(f"Unexpected upload response: {js}")
    except Exception:
        if r.status_code == 200 and r.text.strip():
            return r.text.strip()
        raise

def _load_workflow(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Workflow not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        wf = json.load(f)
    # Ensure shape for /prompt: {"prompt": {...}, "client_id": "..."}
    if not isinstance(wf, dict) or "prompt" not in wf:
        wf = {"prompt": wf}
    if "client_id" not in wf:
        wf["client_id"] = CLIENT_ID
    return wf

def _find_image_nodes(graph: dict) -> T.List[T.Tuple[str, dict]]:
    # Accept both {"prompt":{nodes...}} and flat; operate on wf["prompt"]
    nodes_obj = (graph.get("prompt") if "prompt" in graph else graph)
    # nodes may be dict keyed by node_id or list of nodes
    if isinstance(nodes_obj, dict) and "nodes" in nodes_obj:
        nodes_iter = nodes_obj["nodes"].items() if isinstance(nodes_obj["nodes"], dict) \
                     else ((str(n.get("id", i)), n) for i, n in enumerate(nodes_obj["nodes"]))
    else:
        nodes_iter = nodes_obj.items() if isinstance(nodes_obj, dict) else []

    found = []
    for node_id, node in nodes_iter:
        inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
        if isinstance(inputs, dict):
            for k in inputs.keys():
                if k.lower().startswith("image"):
                    found.append((str(node_id), node))
                    break
    return found

def _inject_filenames(payload: dict, mapping: T.List[T.Tuple[str, str, str]]) -> dict:
    # Operate on payload["prompt"]
    prompt = payload["prompt"]
    nodes = prompt.get("nodes") or prompt
    updated = 0
    if isinstance(nodes, dict):
        for node_id, key, fname in mapping:
            node = nodes.get(node_id)
            if node and isinstance(node.get("inputs"), dict):
                node["inputs"][key] = fname
                updated += 1
    else:
        for node_id, key, fname in mapping:
            for node in nodes:
                if str(node.get("id")) == str(node_id) and isinstance(node.get("inputs"), dict):
                    node["inputs"][key] = fname
                    updated += 1
                    break
    if updated == 0:
        raise RuntimeError("No workflow inputs were updated — check node IDs / keys.")
    return payload

def _post_prompt(payload: dict) -> str:
    r = requests.post(PROMPT_ENDPOINT, json=payload, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    js = r.json()
    pid = js.get("prompt_id") or js.get("promptId") or js.get("id")
    if not pid:
        raise RuntimeError(f"Missing prompt_id in response: {js}")
    return pid

@dataclass
class OutputImage:
    filename: str
    subfolder: str
    type_: str

def _wait_for_result(pid: str, timeout_s: int = 300) -> T.List[OutputImage]:
    t0 = time.time()
    while True:
        r = requests.get(f"{HISTORY_ENDPOINT}/{pid}", timeout=HTTP_TIMEOUT)
        if r.status_code == 404:
            time.sleep(0.8)
        else:
            r.raise_for_status()
            js = r.json()
            hist = js.get("history") or {}
            entry = hist.get(pid) or {}
            outputs = entry.get("outputs") or {}
            imgs: T.List[OutputImage] = []
            for _, node_out in outputs.items():
                for im in (node_out.get("images") or []):
                    imgs.append(OutputImage(
                        filename=im.get("filename"),
                        subfolder=im.get("subfolder", ""),
                        type_=im.get("type", "output"),
                    ))
            if imgs:
                return imgs
            time.sleep(0.8)
        if time.time() - t0 > timeout_s:
            raise TimeoutError(f"Timed out after {timeout_s}s")

def _download_b64(img: OutputImage) -> str:
    params = {"filename": img.filename}
    if img.subfolder: params["subfolder"] = img.subfolder
    if img.type_:     params["type"] = img.type_
    rr = requests.get(VIEW_ENDPOINT, params=params, timeout=HTTP_TIMEOUT)
    rr.raise_for_status()
    return base64.b64encode(rr.content).decode("utf-8")

def rp_handler(event):
    try:
        inp = (event or {}).get("input") or {}
        if "source_b64" in inp: src = _b64_to_bytes(inp["source_b64"])
        elif "source_url" in inp: src = _get_bytes_from_url(inp["source_url"])
        else: return {"error": "Provide 'source_b64' or 'source_url'."}

        if "target_b64" in inp: tgt = _b64_to_bytes(inp["target_b64"])
        elif "target_url" in inp: tgt = _get_bytes_from_url(inp["target_url"])
        else: return {"error": "Provide 'target_b64' or 'target_url'."}

        src_name = _upload("source.png", src)
        tgt_name = _upload("target.png", tgt)

        payload = _load_workflow(WORKFLOW_PATH)

        mapping = inp.get("node_mapping")
        plan: T.List[T.Tuple[str, str, str]] = []
        if mapping and isinstance(mapping, dict):
            s = mapping.get("source"); t = mapping.get("target")
            if not (s and t): return {"error": "node_mapping must include 'source' and 'target'."}
            plan.append((str(s["node_id"]), str(s["input_key"]), src_name))
            plan.append((str(t["node_id"]), str(t["input_key"]), tgt_name))
        else:
            # auto-pick first two image inputs
            cand = _find_image_nodes(payload)
            if len(cand) < 2:
                return {"error": "Could not find two image input nodes; provide 'node_mapping'."}
            def first_key(nd: dict) -> str:
                for k in (nd.get("inputs") or {}):
                    if k.lower().startswith("image"):
                        return k
                raise KeyError("image key not found")
            (sid, s_node), (tid, t_node) = cand[0], cand[1]
            plan.append((sid, first_key(s_node), src_name))
            plan.append((tid, first_key(t_node), tgt_name))

        payload = _inject_filenames(payload, plan)

        pid = _post_prompt(payload)
        images = _wait_for_result(pid, timeout_s=300)
        outs = [_download_b64(img) for img in images]
        return {"status": "ok", "prompt_id": pid, "count": len(outs), "outputs_base64": outs}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

runpod.serverless.start({"handler": rp_handler})
