#!/usr/bin/env python3
"""Split an assembled cut into per-shot clips + shots.json, so a job that
arrives as one master MP4 becomes pipeline-ready (resolve_timecodes,
plan_round and reassemble all read shots.json).

Usage:
  python shot_split.py --cut <master.mp4> --job-dir <dir>
                       [--threshold 0.4] [--min-shot-s 1.0]
                       [--prefix SQ001-SC001] [--edl <file>]
                       [--dry-run] [--force]

Contract:
  - Default mode: ffmpeg scene detection (select='gt(scene,THRESH)' with
    showinfo; cut points are the pts_time values on stderr). --threshold
    is the scene score (0-1); lower finds more cuts. Boundaries closer
    than --min-shot-s to the previous kept boundary are merged away (a
    dissolve or a flash frame is not a shot), and a final boundary that
    would leave a runt tail is dropped. Shot 1 starts at 0; the last shot
    ends at the container duration.
  - --edl mode: detection is skipped and the operator's boundaries are
    authoritative — no merging. One shot START per line; blank lines and
    #-comments ignored. Accepted forms: SS.s / MM:SS / HH:MM:SS /
    HH:MM:SS:FF (frames at 24fps). A leading 0 is optional (implied).
  - Shot ids are <prefix>-SH###, ### = 010, 020, 030... (occ convention:
    gaps leave room for inserts without renumbering the lineup).
  - Writes <job-dir>/clips/<shot_id>.mp4 and <job-dir>/shots.json
    ([{"shot_id", "duration_s", "start_s", "end_s"}] in lineup order,
    floats to 3dp).
  - RE-ENCODE, not stream copy: cuts use output seeking (-ss AFTER -i,
    with -t) and -c:v libx264 -preset veryfast -crf 18 -c:a aac. Input
    seeking with -c copy snaps to the nearest preceding keyframe, which
    on long-GOP masters silently moves shot boundaries by up to a GOP;
    frame-accurate lineups are worth one generation of x264.
  - Verifies the lineup adds up: sum(duration_s), and the sum of the
    written clips' probed runtimes, must each match the master's
    container duration within RUNTIME_TOL_S. A lineup that does not sum
    to the master is a wrong lineup — it fails instead of shipping.
  - --dry-run prints the boundary plan JSON and writes nothing.
  - Refuses to overwrite a non-empty <job-dir>/clips/ or an existing
    shots.json without --force; never destroy a live job's lineup.

Requires ffmpeg/ffprobe on PATH. Errors are JSON on stderr, exit 1.
"""
import argparse, json, os, re, subprocess, sys

from _common import fail, log_invocation

RUNTIME_TOL_S = 0.1     # lineup must sum to the master this tightly
EDL_FPS = 24.0          # frames field of HH:MM:SS:FF timecodes
SHOT_STEP = 10          # SH010, SH020, ... gaps for later inserts
BS = "\\"               # Windows separator, normalized out of stdout paths

def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True, check=True)
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except (KeyError, ValueError):
        fail("no_duration", f"ffprobe reports no container duration for {path}")

def video_duration(path):
    """Video-stream duration. The written-clips verification uses this, not
    the container duration: AAC priming pads each re-encoded clip's audio by
    ~20-45ms, which across dozens of clips sums to a false lineup mismatch."""
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-select_streams",
         "v:0", "-show_streams", path],
        capture_output=True, text=True, check=True)
    try:
        streams = json.loads(out.stdout)["streams"]
        d = streams[0].get("duration")
        return float(d) if d is not None else duration(path)
    except (KeyError, IndexError, ValueError):
        return duration(path)

def detect_cuts(cut, threshold):
    """pts_time of every frame scoring above the scene threshold."""
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", cut,
         "-filter:v", f"select='gt(scene,{threshold})',showinfo",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True)
    if p.returncode != 0:
        fail("detect_failed", f"ffmpeg scene detection failed on {cut}",
             stderr=p.stderr.strip().splitlines()[-3:])
    return sorted(float(m) for m in re.findall(r"pts_time:([0-9.]+)", p.stderr))

def parse_tc(line):
    """SS.s | MM:SS | HH:MM:SS | HH:MM:SS:FF (frames at EDL_FPS) -> seconds."""
    parts = line.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 4:
            h, m, s, f = (int(p) for p in parts)
            return h * 3600 + m * 60 + s + f / EDL_FPS
    except ValueError:
        pass
    fail("bad_timecode", f"cannot read '{line}' as SS.s, MM:SS, HH:MM:SS "
                         f"or HH:MM:SS:FF")

def read_edl(path, total):
    starts = []
    for n, raw in enumerate(open(path), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        t = parse_tc(line)
        if t < 0 or t >= total:
            fail("edl_out_of_range",
                 f"{path}:{n} start {t:.3f}s is outside the {total:.3f}s cut — "
                 f"wrong master or wrong timecode base?")
        starts.append(t)
    if not starts:
        fail("empty_edl", f"{path} lists no shot starts")
    starts = sorted(set(round(t, 3) for t in starts) | {0.0})
    return starts

def merge_boundaries(cuts, total, min_shot_s):
    starts = [0.0]
    for t in cuts:
        if t - starts[-1] >= min_shot_s:
            starts.append(round(t, 3))
    if len(starts) > 1 and total - starts[-1] < min_shot_s:
        starts.pop()            # runt tail: fold it back into the shot before
    return starts

def build_plan(starts, total, prefix):
    plan = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else total
        plan.append({"shot_id": f"{prefix}-SH{(i + 1) * SHOT_STEP:03d}",
                     "duration_s": round(end - start, 3),
                     "start_s": round(start, 3), "end_s": round(end, 3)})
    return plan

def check_sum(label, actual, total):
    if abs(actual - total) > RUNTIME_TOL_S:
        fail("lineup_mismatch",
             f"{label} totals {actual:.3f}s but the master is {total:.3f}s "
             f"(tolerance {RUNTIME_TOL_S}s) — the lineup does not cover the "
             f"cut; diagnose the boundaries before splitting.")

def cut_clip(cut, shot, path):
    """Output seeking (-ss after -i) so the cut lands on the exact frame."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", cut,
         "-ss", f"{shot['start_s']:.3f}", "-t", f"{shot['duration_s']:.3f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-c:a", "aac", path], check=True)

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cut", required=True, help="assembled master to split")
    ap.add_argument("--job-dir", required=True)
    ap.add_argument("--threshold", type=float, default=0.4,
                    help="scene score 0-1 (default 0.4); lower finds more cuts")
    ap.add_argument("--min-shot-s", type=float, default=1.0,
                    help="merge boundaries closer than this (default 1.0)")
    ap.add_argument("--prefix", default="SQ001-SC001", help="shot id prefix")
    ap.add_argument("--edl", help="boundary list; skips scene detection")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the boundary plan and write nothing")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing lineup in --job-dir")
    args = ap.parse_args()

    for f in (args.cut, args.edl):
        if f and not os.path.exists(f):
            fail("no_such_file", f"{f} does not exist")
    if args.threshold <= 0 or args.threshold >= 1:
        fail("bad_threshold", "--threshold must be between 0 and 1 exclusive")

    clips_dir = os.path.join(args.job_dir, "clips")
    manifest = os.path.join(args.job_dir, "shots.json")
    if not args.dry_run and not args.force:
        live = ([manifest] if os.path.exists(manifest) else []) + (
            [clips_dir] if os.path.isdir(clips_dir) and os.listdir(clips_dir) else [])
        if live:
            fail("lineup_exists",
                 f"{args.job_dir} already holds a lineup ({', '.join(live)}) — "
                 f"re-run with --force to replace it")

    total = duration(args.cut)
    if args.edl:
        starts = read_edl(args.edl, total)
    else:
        starts = merge_boundaries(detect_cuts(args.cut, args.threshold),
                                  total, args.min_shot_s)
    plan = build_plan(starts, total, args.prefix)
    check_sum("planned lineup", sum(s["duration_s"] for s in plan), total)

    if args.dry_run:
        print(json.dumps({"cut": args.cut.replace("\\", "/"),
                          "duration_s": round(total, 3),
                          "mode": "edl" if args.edl else "scene_detect",
                          "shots": plan, "dry_run": True}, indent=2))
        return

    os.makedirs(clips_dir, exist_ok=True)
    written = 0.0
    for shot in plan:
        path = os.path.join(clips_dir, shot["shot_id"] + ".mp4")
        cut_clip(args.cut, shot, path)
        written += video_duration(path)
    check_sum("written clips", written, total)

    with open(manifest, "w") as fh:
        json.dump(plan, fh, indent=2)
    log_invocation(args.job_dir, "shot_split",
                   extra={"shots": [s["shot_id"] for s in plan],
                          "mode": "edl" if args.edl else "scene_detect"})
    print(f"OK: {len(plan)} shot(s) from a {total:.2f}s cut -> "
          f"{clips_dir.replace(BS, '/')} (written runtime {written:.2f}s); "
          f"manifest {manifest.replace(BS, '/')}")

if __name__ == "__main__":
    main()
