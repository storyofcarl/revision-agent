#!/usr/bin/env python3
"""Cheap N2 pre-check: PASS/FAIL on pixel shift outside authorized change
regions, so obvious collateral damage fails in seconds — before any
video-qc spend. ffmpeg + numpy only; no model calls.

Usage:
  python diff_gate.py --original <clip> --candidate <clip>
                      --regions <spec.json> [--job-dir <dir>]

Region spec (JSON):
  {
    "authorized": [                       // where change IS allowed
      {"type": "bbox", "data": "x,y,w,h"} //   normalized 0-1, any number
      // or {"type": "fullframe"}         //   whole frame authorized
    ],
    "pixel_delta": 20,                    // gray-level diff (0-255) that
                                          //   counts a pixel as changed
    "area_tolerance": 0.02,               // max fraction of UNAUTHORIZED
                                          //   pixels changed in any frame
    "sample_fps": 2,                      // comparison sampling rate
    "duration_tolerance_s": 0.25          // runtime mismatch allowance
  }
  All fields except "authorized" are optional; defaults shown above are
  the sensible working-res defaults. An empty "authorized" list means
  nothing may change.

Verdict logic: FAIL if runtimes differ beyond tolerance, or if any sampled
frame pair has more than area_tolerance of its unauthorized pixels changed
by more than pixel_delta. Frames are compared at 320x180 grayscale, which
tolerates encoder noise while catching real collateral.

Output: JSON verdict on stdout with per-frame worst offenders and
authorized-region metrics. Exit codes: 0 = PASS, 1 = FAIL (documented,
machine-readable), 2 = usage/tool error (JSON error object on stderr).
"""
import argparse, json, os, subprocess, sys

import numpy as np

from _common import log_invocation

W, H = 320, 180
DEFAULTS = {"pixel_delta": 20, "area_tolerance": 0.02, "sample_fps": 2,
            "duration_tolerance_s": 0.25}

def err(code, message):
    print(json.dumps({"error": code, "message": message}), file=sys.stderr)
    sys.exit(2)

def duration(clip):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", clip],
        capture_output=True, text=True, check=True)
    return float(json.loads(out.stdout)["format"]["duration"])

def gray_frames(clip, fps):
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", clip,
         "-vf", f"fps={fps},scale={W}:{H}", "-pix_fmt", "gray",
         "-f", "rawvideo", "-"],
        capture_output=True, check=True)
    buf = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(buf) // (W * H)
    return buf[:n * W * H].reshape(n, H, W).astype(np.int16)

def build_mask(spec):
    """True where change is AUTHORIZED."""
    mask = np.zeros((H, W), dtype=bool)
    for r in spec.get("authorized", []):
        if r.get("type") == "fullframe":
            mask[:] = True
        elif r.get("type") == "bbox":
            x, y, w, h = (float(v) for v in str(r["data"]).split(","))
            x0, y0 = int(x * W), int(y * H)
            x1, y1 = int(min((x + w), 1.0) * W), int(min((y + h), 1.0) * H)
            mask[y0:y1, x0:x1] = True
        else:
            err("bad_region", f"unknown region type {r.get('type')!r}")
    return mask

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--original", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--regions", required=True, help="region spec JSON path")
    ap.add_argument("--job-dir", help="job directory (logging)")
    args = ap.parse_args()
    for f in (args.original, args.candidate, args.regions):
        if not os.path.exists(f):
            err("no_such_file", f"{f} does not exist")
    spec = {**DEFAULTS, **json.load(open(args.regions))}
    if "authorized" not in spec:
        err("bad_spec", "region spec must contain an 'authorized' list "
                        "(empty list = nothing may change)")

    d_orig, d_cand = duration(args.original), duration(args.candidate)
    result = {"verdict": "PASS", "original": args.original,
              "candidate": args.candidate,
              "duration_s": {"original": round(d_orig, 3),
                             "candidate": round(d_cand, 3)},
              "spec": {k: spec[k] for k in DEFAULTS}, "frames": []}
    if abs(d_orig - d_cand) > spec["duration_tolerance_s"]:
        result["verdict"] = "FAIL"
        result["fail_reason"] = (f"runtime mismatch: {d_orig:.2f}s vs "
                                 f"{d_cand:.2f}s (tol {spec['duration_tolerance_s']}s)")
        print(json.dumps(result, indent=2))
        sys.exit(1)

    fo = gray_frames(args.original, spec["sample_fps"])
    fc = gray_frames(args.candidate, spec["sample_fps"])
    n = min(len(fo), len(fc))
    if n == 0:
        err("no_frames", "could not decode comparable frames from both clips")
    auth = build_mask(spec)
    unauth = ~auth
    unauth_total = int(unauth.sum())
    worst = 0.0
    for i in range(n):
        changed = np.abs(fo[i] - fc[i]) > spec["pixel_delta"]
        out_frac = (float((changed & unauth).sum()) / unauth_total
                    if unauth_total else 0.0)
        in_frac = (float((changed & auth).sum()) / max(int(auth.sum()), 1))
        worst = max(worst, out_frac)
        result["frames"].append({"t": round(i / spec["sample_fps"], 2),
                                 "unauthorized_changed": round(out_frac, 4),
                                 "authorized_changed": round(in_frac, 4)})
    result["worst_unauthorized_changed"] = round(worst, 4)
    if worst > spec["area_tolerance"]:
        result["verdict"] = "FAIL"
        result["fail_reason"] = (f"{worst:.1%} of unauthorized pixels changed "
                                 f"(tolerance {spec['area_tolerance']:.1%}) — "
                                 f"collateral outside the note's region")
    log_invocation(args.job_dir, "diff_gate",
                   extra={"verdict": result["verdict"]})
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["verdict"] == "PASS" else 1)

if __name__ == "__main__":
    main()
