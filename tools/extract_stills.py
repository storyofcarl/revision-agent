#!/usr/bin/env python3
"""Extract key stills from a clip for still-revision methods.

Usage: python extract_stills.py <clip.mp4> <out_dir> [--every N]
Always writes first and last frames; --every N adds one still every N
seconds (use when the noted element moves through the shot).
Requires ffmpeg/ffprobe on PATH.
"""
import json, os, subprocess, sys

def probe(clip):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format",
         "-show_streams", clip], capture_output=True, text=True, check=True)
    meta = json.loads(out.stdout)
    return float(meta["format"]["duration"])

def grab(clip, t, path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i",
                    clip, "-frames:v", "1", "-q:v", "1", path], check=True)

def main():
    clip, out_dir = sys.argv[1], sys.argv[2]
    every = float(sys.argv[sys.argv.index("--every") + 1]) if "--every" in sys.argv else None
    os.makedirs(out_dir, exist_ok=True)
    dur = probe(clip)
    # last frame sampled slightly inside the end to dodge encoder padding
    LAST_INSET_S = 0.05
    times = {"first": 0.0, "last": max(dur - LAST_INSET_S, 0.0)}
    if every:
        t = every
        while t < dur - every / 2:
            times[f"t{t:06.2f}"] = t
            t += every
    for name, t in sorted(times.items(), key=lambda kv: kv[1]):
        grab(clip, t, os.path.join(out_dir, f"{name}.png"))
    print(f"OK: {len(times)} stills from {dur:.2f}s clip -> {out_dir}")

if __name__ == "__main__":
    main()
