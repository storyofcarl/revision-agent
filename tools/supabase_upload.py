#!/usr/bin/env python3
"""Host a local file on Supabase storage and print its public URL.

Ark requires web URLs for reference media; this is the toolchain's single
hosting implementation (replaces the skill's WaveSpeed hosting — WaveSpeed
is a model API only in this repo; see notes-setup.md).

Usage:
  python supabase_upload.py <file> [--cache <uploads.json>] [--job-dir <dir>]
                            [--prefix <bucket-folder>] [--force] [--mock]

Contract:
  - Pre-flight size cap: 100MB. Oversized files exit 1 with a JSON error —
    transcode/downscale the reference first.
  - Cache: keyed by sha256 content hash in revision/uploads.json (or --cache).
    Unexpired entries (< 6 days old, safely inside the 7-day retention) are
    reused without re-uploading; --force bypasses.
  - Retention: cache entries older than 7 days are dropped and their bucket
    objects deleted on every run (public URLs never expire on their own).
  - Output: JSON on stdout {"url", "object", "cached", "sha256"}; exit 0.
    Failures exit non-zero with a JSON error object on stderr.
  - Env: SUPABASE_URL, SUPABASE_KEY, SUPABASE_STORAGE_BUCKET (repo .env).
  - --mock: no network; returns a deterministic fake URL (tests only).
"""
import argparse, hashlib, json, mimetypes, os, sys, time
import requests

import ark_client as ark
from _common import fail, log_invocation

CAP_MB = 100
REUSE_MAX_AGE_S = 6 * 86400      # reuse window
RETENTION_S = 7 * 86400          # delete objects older than this

def _env(name):
    v = os.environ.get(name, "")
    if not v:
        fail("missing_env", f"{name} is not set — populate the repo .env")
    return v

def _headers():
    key = _env("SUPABASE_KEY")
    return {"apikey": key, "Authorization": f"Bearer {key}"}

def _load_cache(path):
    if path and os.path.exists(path):
        return json.load(open(path))
    return {}

def _save_cache(cache, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    json.dump(cache, open(path, "w"), indent=2)

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def _cleanup_expired(cache, mock=False):
    """Drop cache entries past retention and delete their bucket objects."""
    now = time.time()
    for k in list(cache):
        ent = cache[k]
        if now - ent.get("ts", 0) > RETENTION_S:
            if not mock:
                base, bucket = _env("SUPABASE_URL").rstrip("/"), _env("SUPABASE_STORAGE_BUCKET")
                try:
                    requests.delete(f"{base}/storage/v1/object/{bucket}/{ent['object']}",
                                    headers=_headers(), timeout=60)
                except requests.RequestException:
                    pass   # object may already be gone; cache drop still proceeds
            del cache[k]

def upload(path, cache_path, prefix="uploads", force=False, mock=False):
    """Python API used by ark_client/batch_generate/white_render/image_edit.
    Returns {"url", "object", "cached", "sha256"}."""
    if not os.path.exists(path):
        fail("no_such_file", f"{path} does not exist")
    size_mb = os.path.getsize(path) / 1e6
    if size_mb > CAP_MB:
        fail("over_cap", f"{path} is {size_mb:.0f}MB — over the {CAP_MB}MB hosting "
             f"cap. Transcode/downscale the reference first.", size_mb=round(size_mb, 1))
    digest = sha256_file(path)
    cache = _load_cache(cache_path)
    _cleanup_expired(cache, mock=mock)
    ent = cache.get(digest)
    if ent and not force and time.time() - ent["ts"] < REUSE_MAX_AGE_S:
        _save_cache(cache, cache_path)
        return {"url": ent["url"], "object": ent["object"], "cached": True,
                "sha256": digest}
    obj = f"{prefix}/{digest[:16]}_{os.path.basename(path)}"
    if mock:
        url = f"https://mock.supabase.local/storage/v1/object/public/mock-bucket/{obj}"
    else:
        base, bucket = _env("SUPABASE_URL").rstrip("/"), _env("SUPABASE_STORAGE_BUCKET")
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            r = requests.post(f"{base}/storage/v1/object/{bucket}/{obj}",
                              headers={**_headers(), "Content-Type": mime,
                                       "x-upsert": "true"},
                              data=fh, timeout=600)
        if r.status_code >= 400:
            fail("upload_failed", f"Supabase returned {r.status_code}: {r.text[:300]}")
        url = f"{base}/storage/v1/object/public/{bucket}/{obj}"
    cache[digest] = {"url": url, "object": obj, "ts": time.time(),
                     "src": os.path.abspath(path)}
    _save_cache(cache, cache_path)
    return {"url": url, "object": obj, "cached": False, "sha256": digest}

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="local file to host")
    ap.add_argument("--cache", help="uploads cache path (default "
                                    "<job-dir>/revision/uploads.json)")
    ap.add_argument("--job-dir", help="job directory (cache location + logging)")
    ap.add_argument("--prefix", default=None,
                    help="bucket folder (default: job id, else 'uploads')")
    ap.add_argument("--force", action="store_true", help="bypass the cache")
    ap.add_argument("--mock", action="store_true",
                    help="no network; deterministic fake URL (tests only)")
    args = ap.parse_args()
    ark.load_env()
    cache_path = args.cache or (os.path.join(args.job_dir, "revision", "uploads.json")
                                if args.job_dir else "uploads.json")
    prefix = args.prefix or (os.path.basename(os.path.abspath(args.job_dir))
                             if args.job_dir else "uploads")
    res = upload(args.file, cache_path, prefix=prefix, force=args.force,
                 mock=args.mock)
    log_invocation(args.job_dir, "supabase_upload", extra={"result": res})
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
