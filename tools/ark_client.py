#!/usr/bin/env python3
"""Shared client for BytePlus Ark (Seedance video tasks, Seedream images).
Mirrors occ's production-verified Ark backend:
POST /contents/generations/tasks -> poll GET /{id} -> succeeded|failed.

Reference hosting (Ark needs web URLs) is Supabase storage via
supabase_upload.py — see notes-setup.md; WaveSpeed is a model API only here.

Keys load from the repo .env (ARK_API_KEY, SUPABASE_*) — never on the
command line, never logged.
"""
import base64, json, mimetypes, os, sys, time
import requests

ARK_BASE_DEFAULT = "https://ark.ap-southeast.bytepluses.com/api/v3"
# Account inference endpoints (override via ARK_EP_* in the skill .env)
ENDPOINTS = {
    "seedance_pro":   "ep-20260508193017-pjb8t",
    "seedance_fast":  "ep-20260626055930-mnztv",
    "seedance_mini":  "ep-20260626055125-lst2w",
    "seedream_5_pro": "ep-20260708211633-gjng5",
    "seedream_5_lite": "ep-20260720040538-hchm2",
}
POLL_INTERVAL_S = 5      # matches occ default; tasks run minutes
POLL_TIMEOUT_S = 900     # matches occ default

def load_env():
    """Load keys from, in order of precedence (first hit per key wins):
    1. an explicit STUDIO_ENV=<path> file, 2. this repo's own .env,
    3. the sibling occ install's .env (../occ-longform-video/.env) — the
    studio convention: occ's .env is the shared key store, so repos
    installed alongside it need no copies."""
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [os.environ.get("STUDIO_ENV"),
                  os.path.join(repo_dir, ".env"),
                  os.path.join(os.path.dirname(repo_dir), "occ-longform-video", ".env")]
    for env_path in candidates:
        if env_path and os.path.exists(env_path):
            for line in open(env_path):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for k in list(ENDPOINTS):
        ENDPOINTS[k] = os.environ.get(f"ARK_EP_{k.upper()}", ENDPOINTS[k])

def _key(name):
    v = os.environ.get(name, "")
    if not v or any(t in v.lower() for t in ("your_", "example", "replace", "changeme")):
        sys.exit(f"Missing/placeholder {name} — set a real key in the repo .env")
    return v

def ark_base():
    return os.environ.get("ARK_BASE_URL", ARK_BASE_DEFAULT).rstrip("/")

def data_url(path):
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(open(path, "rb").read()).decode()

def is_remote(s):
    return isinstance(s, str) and s.startswith(("http://", "https://"))

# ---------------- Hosting (reference_video needs a web URL) ---------------
def upload_hosted(path, cache_path):
    """Upload a local file to Supabase storage, returning a public web URL.
    Delegates to supabase_upload.py (content-hash cache, 100MB cap, 7-day
    retention) — one hosting implementation for the whole toolchain."""
    import supabase_upload
    return supabase_upload.upload(path, cache_path)["url"]

def proxy_clip(path, out_path, height=480):
    """Working-res proxy for hosting reference video. Ark fetches the URL
    itself and TIMES OUT on hi-res sources (a 60MB 4K clip failed on the
    Spookley pilot); it renders at working res anyway, so host a proxy."""
    import subprocess
    if not os.path.exists(out_path):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path,
                        "-vf", f"scale=-2:{height}", "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "23", "-an", out_path],
                       check=True)
    return out_path

# ---------------- Seedance video task -------------------------------------
def build_content(prompt, ref_video_url=None, first_frame=None, last_frame=None,
                  reference_images=(), ref_audio=None):
    """Content order mirrors occ: text, reference_video, first/last frames,
    then reference_images (addressed as @Image1.. in that order)."""
    content = [{"type": "text", "text": prompt}]
    if ref_video_url:
        if not is_remote(ref_video_url):
            raise ValueError("reference_video must be a web URL — upload local clips first")
        content.append({"type": "video_url", "video_url": {"url": ref_video_url},
                        "role": "reference_video"})
    for img, role in ((first_frame, "first_frame"), (last_frame, "last_frame")):
        if img:
            u = img if is_remote(img) else data_url(img)
            content.append({"type": "image_url", "image_url": {"url": u}, "role": role})
    for img in reference_images:
        u = img if is_remote(img) else data_url(img)
        content.append({"type": "image_url", "image_url": {"url": u}, "role": "reference_image"})
    if ref_audio:
        u = ref_audio if is_remote(ref_audio) else data_url(ref_audio)
        content.append({"type": "audio_url", "audio_url": {"url": u}, "role": "reference_audio"})
    return content

def submit_video(model_ep, content, *, ratio, resolution, duration, generate_audio, seed=None):
    payload = {"model": model_ep, "content": content, "ratio": ratio,
               "duration": max(4, min(15, int(duration))),   # Seedance 4-15s window
               "resolution": resolution, "watermark": False,
               "generate_audio": bool(generate_audio)}
    if seed is not None:
        payload["seed"] = seed
    r = requests.post(f"{ark_base()}/contents/generations/tasks", json=payload,
                      headers={"Authorization": f"Bearer {_key('ARK_API_KEY')}",
                               "Content-Type": "application/json"}, timeout=120)
    if r.status_code >= 400:
        # surface Ark's error body — a bare 400 is undiagnosable
        raise RuntimeError(f"Ark submit {r.status_code}: {r.text[:300]}")
    tid = r.json().get("id")
    if not tid:
        sys.exit(f"Task creation returned no id: {json.dumps(r.json())[:300]}")
    return str(tid)

def poll_video(task_id):
    """Poll one task to completion. Returns the video URL or raises."""
    headers = {"Authorization": f"Bearer {_key('ARK_API_KEY')}"}
    start = time.time()
    while True:
        if time.time() - start > POLL_TIMEOUT_S:
            raise TimeoutError(f"task {task_id} timed out after {POLL_TIMEOUT_S}s")
        r = requests.get(f"{ark_base()}/contents/generations/tasks/{task_id}",
                         headers=headers, timeout=120)
        r.raise_for_status()
        res = r.json()
        st = res.get("status")
        if st == "succeeded":
            return _find_video_url(res)
        if st == "failed":
            raise RuntimeError(f"task {task_id} failed: "
                               f"{json.dumps(res.get('error', res))[:300]}")
        time.sleep(POLL_INTERVAL_S)

def check_video(task_id):
    """Non-blocking status check -> (status, url_or_None, raw)."""
    headers = {"Authorization": f"Bearer {_key('ARK_API_KEY')}"}
    r = requests.get(f"{ark_base()}/contents/generations/tasks/{task_id}",
                     headers=headers, timeout=120)
    r.raise_for_status()
    res = r.json()
    st = res.get("status")
    return st, (_find_video_url(res) if st == "succeeded" else None), res

def _find_video_url(node, depth=0):
    if depth > 6:
        return None
    if isinstance(node, dict):
        for k in ("video_url", "url", "video_uri"):
            v = node.get(k)
            if isinstance(v, str) and v.startswith(("http://", "https://", "tos://")):
                return v
            if isinstance(v, dict):
                f = _find_video_url(v, depth + 1)
                if f:
                    return f
        for v in node.values():
            if isinstance(v, (dict, list)):
                f = _find_video_url(v, depth + 1)
                if f:
                    return f
    elif isinstance(node, list):
        for it in node:
            f = _find_video_url(it, depth + 1)
            if f:
                return f
    return None

def download(url, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(out_path, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    return out_path

# ---------------- Seedream image (synchronous) -----------------------------
def seedream_image(model_ep, prompt, *, image=None, size="2K", out_path):
    payload = {"model": model_ep, "prompt": prompt, "size": size,
               "response_format": "url", "watermark": False}
    if image:
        payload["image"] = image if is_remote(image) else data_url(image)
    r = requests.post(f"{ark_base()}/images/generations", json=payload,
                      headers={"Authorization": f"Bearer {_key('ARK_API_KEY')}",
                               "Content-Type": "application/json"}, timeout=300)
    r.raise_for_status()
    url = r.json()["data"][0]["url"]
    return download(url, out_path)   # Ark image URLs expire in 24h — persist now
