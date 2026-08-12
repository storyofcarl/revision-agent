#!/usr/bin/env python3
"""Edit a still with one of the three routed editors, writing a versioned
output that never overwrites.

Usage:
  python image_edit.py --still <path> --prompt <text>
                       [--model {seedream5pro|nanobananapro|gptimage2}]
                       [--out-dir <dir>] [--job-dir <dir>] [--mock]

Contract:
  - Default editor: Seedream 5 Pro (per references/routing-and-costs.md
    §Still editors); nanobananapro (strongest text-in-image) and gptimage2
    are fallbacks. Uniform interface — routing is a flag, not a rewrite.
  - Providers: seedream5pro runs on Ark (ARK_API_KEY); nanobananapro and
    gptimage2 run on the WaveSpeed API (WAVESPEED_API_KEY) — model IDs
    google/nano-banana-pro/edit and openai/gpt-image-2/edit, overridable
    via WS_MODEL_NANOBANANA / WS_MODEL_GPTIMAGE env vars.
  - --refs <paths...>: reference images (character sheets, target frames)
    sent alongside the still — the WaveSpeed editors accept multiple
    images; the prompt should address them by order ("image 2 is the
    official character sheet"). Not supported by seedream5pro (single
    image input on Ark) — using --refs there is an error, not a silent
    drop.
  - Output: <out-dir>/<still-stem>_v<N>.png, N auto-incremented, never
    overwriting. Default out-dir: <job-dir>/stills/edited (or ./edited).
  - Stdout: JSON {"out", "model", "version"}; exit 0. Failures exit
    non-zero with a JSON error object on stderr.
  - --mock: no network, no spend; copies the input still to the versioned
    output path so naming/versioning is testable.
"""
import argparse, glob, json, os, re, shutil, sys, time
import requests

import ark_client as ark
import supabase_upload
from _common import fail, log_invocation

WS_BASE = "https://api.wavespeed.ai/api/v3"
WS_MODELS = {
    "nanobananapro": os.environ.get("WS_MODEL_NANOBANANA",
                                    "google/nano-banana-pro/edit"),
    "gptimage2": os.environ.get("WS_MODEL_GPTIMAGE",
                                "openai/gpt-image-2/edit"),
}
WS_POLL_INTERVAL_S = 4
WS_POLL_TIMEOUT_S = 300

def next_version_path(still, out_dir):
    """<out_dir>/<stem>_v<N>.png with N = 1 + highest existing."""
    stem = re.sub(r"_v\d+$", "", os.path.splitext(os.path.basename(still))[0])
    existing = glob.glob(os.path.join(out_dir, f"{stem}_v*.png"))
    ns = [int(m.group(1)) for p in existing
          if (m := re.search(r"_v(\d+)\.png$", p))]
    n = max(ns, default=0) + 1
    return os.path.join(out_dir, f"{stem}_v{n}.png"), n

def edit_wavespeed(model_key, still, prompt, out_path, cache_path, refs=()):
    """Submit to a WaveSpeed edit model, poll /predictions/{id}/result,
    download the first output. refs are additional reference images
    (character sheets, target frames) appended after the still."""
    hosted = supabase_upload.upload(still, cache_path, prefix="stills")["url"]
    images = [hosted] + [supabase_upload.upload(p, cache_path, prefix="refs")["url"]
                         for p in refs]
    key = os.environ.get("WAVESPEED_API_KEY", "")
    if not key:
        fail("missing_env", "WAVESPEED_API_KEY is not set — populate the repo .env")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    r = requests.post(f"{WS_BASE}/{WS_MODELS[model_key]}", headers=headers,
                      json={"prompt": prompt, "images": images,
                            "output_format": "png"}, timeout=120)
    if r.status_code >= 400:
        fail("submit_failed", f"WaveSpeed returned {r.status_code}: {r.text[:300]}")
    task_id = r.json()["data"]["id"]
    start = time.time()
    while True:
        if time.time() - start > WS_POLL_TIMEOUT_S:
            fail("timeout", f"WaveSpeed task {task_id} exceeded {WS_POLL_TIMEOUT_S}s")
        pr = requests.get(f"{WS_BASE}/predictions/{task_id}/result",
                          headers={"Authorization": f"Bearer {key}"}, timeout=60)
        pr.raise_for_status()
        data = pr.json()["data"]
        if data["status"] == "completed":
            ark.download(data["outputs"][0], out_path)
            return
        if data["status"] == "failed":
            fail("edit_failed", f"WaveSpeed task {task_id}: "
                 f"{str(data.get('error', 'unknown'))[:300]}")
        time.sleep(WS_POLL_INTERVAL_S)

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--still", required=True, help="input still (png/jpg)")
    ap.add_argument("--prompt", required=True, help="edit instruction")
    ap.add_argument("--model", default="seedream5pro",
                    choices=["seedream5pro", "nanobananapro", "gptimage2"],
                    help="editor (default seedream5pro per routing-and-costs)")
    ap.add_argument("--out-dir", help="output dir (default <job-dir>/stills/edited)")
    ap.add_argument("--job-dir", help="job directory (output location + logging)")
    ap.add_argument("--refs", nargs="*", default=[],
                    help="reference images sent with the still (WaveSpeed "
                         "editors only); address them by order in the prompt")
    ap.add_argument("--mock", action="store_true",
                    help="no network/spend; copy input to versioned output (tests)")
    args = ap.parse_args()
    ark.load_env()
    if not os.path.exists(args.still):
        fail("no_such_file", f"{args.still} does not exist")
    for p in args.refs:
        if not os.path.exists(p):
            fail("no_such_file", f"--refs {p} does not exist")
    if args.refs and args.model == "seedream5pro" and not args.mock:
        fail("refs_unsupported", "seedream5pro takes a single image — use "
             "nanobananapro or gptimage2 for reference-guided edits")
    out_dir = args.out_dir or (os.path.join(args.job_dir, "stills", "edited")
                               if args.job_dir else "edited")
    os.makedirs(out_dir, exist_ok=True)
    out_path, version = next_version_path(args.still, out_dir)
    cache_path = (os.path.join(args.job_dir, "revision", "uploads.json")
                  if args.job_dir else "uploads.json")

    if args.mock:
        shutil.copyfile(args.still, out_path)
    elif args.model == "seedream5pro":
        ark.seedream_image(ark.ENDPOINTS["seedream_5_pro"], args.prompt,
                           image=args.still, out_path=out_path)
    else:
        edit_wavespeed(args.model, args.still, args.prompt, out_path, cache_path,
                       refs=args.refs)

    res = {"out": out_path.replace("\\", "/"), "model": args.model,
           "version": version}
    log_invocation(args.job_dir, "image_edit", extra={"result": res})
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
