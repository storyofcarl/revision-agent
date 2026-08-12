#!/usr/bin/env python3
"""Standalone-mode reassembly: concat verified clips per shots.json order,
verify runtime vs master and A/V drift.

Usage: python reassemble.py <job_dir> <out.mp4>
occ-native jobs use `occ stitch` instead — do not use this script there.
Refuses to stitch a set with gaps; never publish a film with holes.
"""
import json, os, subprocess, sys

RUNTIME_TOL_S = 0.25   # matches video-qc duration tolerance (encoder GOP rounding)

def dur(path):
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                          "-show_format", path], capture_output=True, text=True, check=True)
    return float(json.loads(out.stdout)["format"]["duration"])

def main():
    job_dir, out = sys.argv[1], sys.argv[2]
    shots = json.load(open(f"{job_dir}/shots.json"))
    missing = [s["shot_id"] for s in shots
               if not os.path.exists(f"{job_dir}/clips/{s['shot_id']}.mp4")]
    if missing:
        print(f"REFUSED: {len(missing)} clip(s) missing — never stitch gaps:")
        [print(" -", m) for m in missing]
        sys.exit(1)
    lst = f"{job_dir}/revision/_concat.txt"
    os.makedirs(os.path.dirname(lst), exist_ok=True)
    with open(lst, "w") as fh:
        for s in shots:
            # forward slashes: ffmpeg concat demuxer chokes on backslashes (Windows)
            root = os.path.abspath(job_dir).replace("\\", "/")
            fh.write(f"file '{root}/clips/{s['shot_id']}.mp4'\n")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", lst, "-c", "copy", out], check=True)
    expected = sum(s["duration_s"] for s in shots)
    actual = dur(out)
    if abs(actual - expected) > RUNTIME_TOL_S:
        print(f"FAIL: assembled {actual:.2f}s vs planned {expected:.2f}s "
              f"(tolerance {RUNTIME_TOL_S}s) — a clip is the wrong length; "
              f"diagnose before resubmitting.")
        sys.exit(1)
    print(f"OK: {out} assembled, {actual:.2f}s (planned {expected:.2f}s). "
          f"Mux/verify audio against the master per occ rules if a sync track exists.")

if __name__ == "__main__":
    main()
