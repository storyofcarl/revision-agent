#!/usr/bin/env python3
"""EDL-aware working-res assembly of a revision cut: swap verified
candidates into the lineup, apply editorial ops (inserts, trims), mux the
master's audio, and refuse to ship picture that does not match it.

Usage:
  python assemble_cut.py <job_dir> --out <out.mp4> --plan <assembly.json>
                         [--dry-run]

Replaces reassemble.py for rounds that change the cut. reassemble.py
stream-copies clips/ in shots.json order and has no audio; this tool
re-encodes every segment to one working-res spec, so a candidate at a
different resolution, frame rate or pixel format concatenates cleanly.
occ-native jobs use `occ stitch` instead.

assembly.json:
  {"width": 992, "height": 432, "fps": 24,
   "audio_master": "S4DJ_song_cleanup_081126_cr.mp4",
   "swaps": {"SQ001-SC001-SH130": "candidates/conformed/N-001.mp4"},
   "edl": [{"shot": "SQ001-SC001-SH010"},
           {"file": "candidates/conformed/N-014.mp4"},
           {"shot": "SQ001-SC001-SH520", "trim": [0, 0.41]}]}

Contract:
  - width/height/fps are the working-res spec; audio_master is the mix
    whose audio track is muxed verbatim. Paths in the plan are
    job-relative (an absolute or cwd-relative path also resolves).
  - edl is the ORDERED timeline. A segment is either {"shot": id} (the
    lineup clip, or its replacement if the shot is in swaps) or
    {"file": path} (an arbitrary clip — an insert). Either may carry
    "trim": [start_s, end_s], seconds within THAT source. Omit edl and
    the timeline is shots.json order with swaps applied.
  - Every segment is normalized to width x height (aspect preserved,
    padded), fps, yuv420p, libx264 crf 18 veryfast, NO audio, into
    <job_dir>/revision/assembly_segments/<hash>.mp4. The hash covers the
    source path, its size and mtime, the trim and the encode spec, so a
    re-run re-uses segments it already encoded and re-encodes the ones
    whose source changed.
  - PROTECTED files are inputs, never outputs: nothing is ever written
    inside <job_dir>/candidates/ (--out included).
  - Segments concat (stream copy) to <job_dir>/revision/assembly_video.mp4,
    then the master's audio is muxed on: -c:a copy when the codec is
    mp4-safe, else aac 192k. The concat's VIDEO duration must match the
    master's AUDIO duration within RUNTIME_TOL_S or the run fails with
    actual vs expected and --out is never written — un-synced picture is
    worse than no picture, and the fix is an editorial one (a segment is
    the wrong length), not a re-mux.
  - --dry-run prints the resolved timeline (per-segment source, trim,
    duration, running total; expected total vs audio duration) as JSON
    and writes nothing.

Requires ffmpeg/ffprobe on PATH. Errors are JSON on stderr, exit 1.
"""
import argparse, hashlib, json, os, subprocess, sys

from _common import fail, log_invocation

RUNTIME_TOL_S = 0.25        # matches video-qc / reassemble A-V tolerance
SEG_DIR = "assembly_segments"
PROTECTED = ("candidates",)  # job subdirs that are inputs, never outputs
# mp4-safe audio codecs: anything else gets re-encoded rather than risk a
# container that some players refuse
COPY_OK = ("aac", "mp3", "ac3", "eac3", "alac")
BS = "\\"                   # Windows separator, normalized out of stdout paths

def _probe(path, *args):
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                          *args, path], capture_output=True, text=True)
    if out.returncode != 0:
        fail("probe_failed", f"ffprobe cannot read {path}")
    try:
        return json.loads(out.stdout)
    except ValueError:
        fail("probe_failed", f"ffprobe returned no JSON for {path}")

def duration(path):
    try:
        return float(_probe(path, "-show_format")["format"]["duration"])
    except (KeyError, ValueError):
        fail("no_duration", f"ffprobe reports no container duration for {path}")

def stream_duration(path, kind):
    """Duration of the first video/audio stream, falling back to the
    container. The A-V check uses stream durations: AAC priming and the
    container's rounded duration each shift the answer by tens of ms, and
    the tolerance here is only 250ms."""
    d = _probe(path, "-select_streams", kind[0] + ":0", "-show_streams")
    streams = d.get("streams") or []
    if not streams:
        return None
    v = streams[0].get("duration")
    try:
        return float(v)
    except (TypeError, ValueError):
        return duration(path)

def audio_codec(path):
    streams = _probe(path, "-select_streams", "a:0", "-show_streams").get("streams") or []
    return streams[0].get("codec_name") if streams else None

def resolve(job_dir, path, what):
    """Plan paths are job-relative; an absolute/cwd-relative path also works."""
    job_rel = os.path.join(job_dir, path)
    if os.path.exists(job_rel):
        return job_rel
    if os.path.exists(path):
        return path
    fail("no_such_file",
         f"{what}: '{path}' does not exist (tried "
         f"{job_rel.replace(BS, '/')} and {path.replace(BS, '/')})")

def refuse_protected(job_dir, path, what):
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(job_dir))
    except ValueError:      # different Windows drive: cannot be inside the job
        return
    head = rel.replace(BS, "/").split("/")[0]
    if not rel.startswith("..") and head in PROTECTED:
        fail("protected_output",
             f"{what} would write into {job_dir.replace(BS, '/')}/{head}/ — "
             f"{head}/ holds PROTECTED inputs (generated candidates and their "
             f"lineage) and is never an output directory; write the cut "
             f"somewhere else")

def load_plan(path):
    try:
        plan = json.load(open(path, encoding="utf-8-sig"))
    except ValueError as e:
        fail("bad_plan", f"{path} is not valid JSON: {e}")
    if not isinstance(plan, dict):
        fail("bad_plan", f"{path} must be a JSON object")
    for key in ("width", "height", "fps", "audio_master"):
        if key not in plan:
            fail("bad_plan", f"{path} is missing required key '{key}'")
    for key in ("width", "height", "fps"):
        try:
            if float(plan[key]) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            fail("bad_plan", f"{path}: '{key}' must be a positive number, "
                             f"got {plan[key]!r}")
    if not isinstance(plan.get("swaps", {}), dict):
        fail("bad_plan", f"{path}: 'swaps' must be an object of shot -> path")
    if "edl" in plan and not isinstance(plan["edl"], list):
        fail("bad_plan", f"{path}: 'edl' must be a list of segments")
    return plan

def read_edl(plan, job_dir):
    """Resolve the plan into an ordered list of raw segments (shot/file +
    trim), before any probing."""
    swaps = plan.get("swaps", {})
    edl = plan.get("edl")
    if edl is None:
        manifest = os.path.join(job_dir, "shots.json")
        if not os.path.exists(manifest):
            fail("no_such_file",
                 f"no 'edl' in the plan and no {manifest.replace(BS, '/')} to "
                 f"take the lineup order from")
        try:
            shots = json.load(open(manifest))
        except ValueError as e:
            fail("bad_manifest", f"{manifest} is not valid JSON: {e}")
        edl = [{"shot": s["shot_id"]} for s in shots]
    if not edl:
        fail("empty_timeline", "the resolved timeline has no segments — "
                               "nothing to assemble")
    segs = []
    for i, raw in enumerate(edl):
        if not isinstance(raw, dict) or ("shot" in raw) == ("file" in raw):
            fail("bad_segment",
                 f"segment {i}: needs exactly one of 'shot' or 'file', "
                 f"got {json.dumps(raw)}")
        if "shot" in raw:
            shot = raw["shot"]
            swapped = shot in swaps
            src = swaps[shot] if swapped else f"clips/{shot}.mp4"
            what = (f"segment {i} shot {shot} swap" if swapped
                    else f"segment {i} shot {shot}")
        else:
            shot, swapped, src = None, False, raw["file"]
            what = f"segment {i} file"
        trim = raw.get("trim")
        if trim is not None:
            try:
                trim = [float(trim[0]), float(trim[1])]
            except (TypeError, ValueError, IndexError):
                fail("bad_trim", f"segment {i}: 'trim' must be "
                                 f"[start_s, end_s], got {json.dumps(raw.get('trim'))}")
        segs.append({"index": i, "shot": shot, "swapped": swapped,
                     "spec_path": src, "path": resolve(job_dir, src, what),
                     "trim": trim})
    return segs

def plan_timeline(segs, w, h, fps, seg_dir):
    """Probe each source, validate trims, and attach the cache key, the
    planned duration and the running total."""
    total = 0.0
    for s in segs:
        src_dur = duration(s["path"])
        if s["trim"] is None:
            s["duration_s"] = round(src_dur, 3)
        else:
            start, end = s["trim"]
            if start < 0 or end <= start:
                fail("bad_trim",
                     f"segment {s['index']}: trim [{start}, {end}] is not an "
                     f"increasing, non-negative range")
            if end > src_dur + RUNTIME_TOL_S:
                fail("bad_trim",
                     f"segment {s['index']}: trim [{start}, {end}] runs past "
                     f"the end of {s['spec_path']} ({src_dur:.3f}s) — wrong "
                     f"source, or the trim is in timeline seconds instead of "
                     f"seconds within the shot")
            s["duration_s"] = round(min(end, src_dur) - start, 3)
        st = os.stat(s["path"])
        key = json.dumps({"src": os.path.abspath(s["path"]).replace(BS, "/"),
                          "size": st.st_size, "mtime": int(st.st_mtime),
                          "trim": s["trim"], "w": w, "h": h, "fps": fps,
                          "enc": "libx264/veryfast/crf18/yuv420p/noaudio"},
                         sort_keys=True)
        s["hash"] = hashlib.sha256(key.encode()).hexdigest()[:16]
        s["segment"] = os.path.join(seg_dir, s["hash"] + ".mp4")
        s["cached"] = os.path.exists(s["segment"])
        total = round(total + s["duration_s"], 3)
        s["running_total_s"] = total
    return total

def encode_segment(seg, w, h, fps):
    """One segment -> one normalized, audio-free clip. Output seeking
    (-ss AFTER -i) so a trim lands on the intended frame instead of the
    nearest preceding keyframe."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", seg["path"]]
    if seg["trim"] is not None:
        cmd += ["-ss", f"{seg['trim'][0]:.3f}", "-t", f"{seg['duration_s']:.3f}"]
    vf = (f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    cmd += ["-vf", vf, "-pix_fmt", "yuv420p", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "18", "-an", seg["segment"]]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        if os.path.exists(seg["segment"]):
            os.unlink(seg["segment"])   # never leave a half-written cache hit
        fail("encode_failed",
             f"segment {seg['index']} ({seg['spec_path']}) failed to normalize",
             stderr=p.stderr.strip().splitlines()[-3:])

def concat(segs, list_path, out_path):
    with open(list_path, "w") as fh:
        for s in segs:
            # forward slashes: the concat demuxer chokes on backslashes
            fh.write(f"file '{os.path.abspath(s['segment']).replace(BS, '/')}'\n")
    p = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat",
                        "-safe", "0", "-i", list_path, "-c", "copy", out_path],
                       capture_output=True, text=True)
    if p.returncode != 0:
        fail("concat_failed", "concatenating the normalized segments failed",
             stderr=p.stderr.strip().splitlines()[-3:])

def mux(video, master, codec, out_path):
    audio = ["-c:a", "copy"] if codec in COPY_OK else ["-c:a", "aac", "-b:a", "192k"]
    p = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", video,
                        "-i", master, "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "copy", *audio, "-movflags", "+faststart",
                        out_path], capture_output=True, text=True)
    if p.returncode != 0:
        fail("mux_failed", f"muxing audio from {master} onto the cut failed",
             stderr=p.stderr.strip().splitlines()[-3:])
    return "copy" if codec in COPY_OK else "aac192k"

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_dir")
    ap.add_argument("--out", required=True, help="assembled cut to write")
    ap.add_argument("--plan", required=True, help="assembly.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the resolved timeline and write nothing")
    args = ap.parse_args()

    job_dir = args.job_dir.rstrip("/\\")
    if not os.path.isdir(job_dir):
        fail("no_such_dir", f"{job_dir} is not a directory")
    if not os.path.exists(args.plan):
        fail("no_such_file", f"{args.plan} does not exist")

    plan = load_plan(args.plan)
    w, h = int(plan["width"]), int(plan["height"])
    fps = plan["fps"]
    master = resolve(job_dir, plan["audio_master"], "audio_master")
    codec = audio_codec(master)
    if not codec:
        fail("no_audio_stream",
             f"audio_master {plan['audio_master']} has no audio stream — "
             f"a cut assembled against it would ship mute")
    audio_dur = stream_duration(master, "audio") or duration(master)

    seg_dir = os.path.join(job_dir, "revision", SEG_DIR)
    segs = read_edl(plan, job_dir)
    expected = plan_timeline(segs, w, h, fps, seg_dir)

    def report(seg):
        return {"index": seg["index"], "shot": seg["shot"],
                "source": seg["spec_path"], "swapped": seg["swapped"],
                "trim": seg["trim"], "duration_s": seg["duration_s"],
                "running_total_s": seg["running_total_s"],
                "segment": os.path.relpath(seg["segment"], job_dir).replace(BS, "/"),
                "cached": seg["cached"]}

    if args.dry_run:
        print(json.dumps(
            {"job_dir": job_dir.replace(BS, "/"),
             "out": args.out.replace(BS, "/"),
             "working_res": f"{w}x{h}", "fps": fps,
             "source": "edl" if plan.get("edl") is not None else "shots.json",
             "audio_master": plan["audio_master"],
             "audio_codec": codec, "audio_duration_s": round(audio_dur, 3),
             "segments": [report(s) for s in segs],
             "expected_total_s": round(expected, 3),
             "delta_s": round(expected - audio_dur, 3),
             "within_tolerance": abs(expected - audio_dur) <= RUNTIME_TOL_S,
             "tolerance_s": RUNTIME_TOL_S, "dry_run": True}, indent=2))
        return

    refuse_protected(job_dir, args.out, "--out")
    refuse_protected(job_dir, seg_dir, "the segment cache")
    out_parent = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_parent, exist_ok=True)
    os.makedirs(seg_dir, exist_ok=True)

    encoded = 0
    for seg in segs:
        if seg["cached"]:
            continue
        encode_segment(seg, w, h, fps)
        encoded += 1

    video = os.path.join(job_dir, "revision", "assembly_video.mp4")
    concat(segs, os.path.join(job_dir, "revision", "_assembly_concat.txt"), video)
    actual = stream_duration(video, "video") or duration(video)
    if abs(actual - audio_dur) > RUNTIME_TOL_S:
        fail("av_duration_mismatch",
             f"assembled picture is {actual:.3f}s but the audio master "
             f"{plan['audio_master']} is {audio_dur:.3f}s "
             f"(delta {actual - audio_dur:+.3f}s, tolerance {RUNTIME_TOL_S}s) — "
             f"a segment is the wrong length; fix the timeline, never ship "
             f"un-synced picture. Nothing was written to "
             f"{args.out.replace(BS, '/')}.",
             actual_video_s=round(actual, 3), expected_audio_s=round(audio_dur, 3),
             planned_total_s=round(expected, 3), tolerance_s=RUNTIME_TOL_S,
             segments=[{"index": s["index"], "shot": s["shot"],
                        "source": s["spec_path"],
                        "duration_s": s["duration_s"]} for s in segs])

    mode = mux(video, master, codec, args.out)
    log_invocation(job_dir, "assemble_cut",
                   extra={"out": args.out.replace(BS, "/"),
                          "segments": len(segs), "encoded": encoded,
                          "cached": len(segs) - encoded,
                          "duration_s": round(actual, 3),
                          "audio_master": plan["audio_master"],
                          "audio_mode": mode,
                          "swapped": [s["shot"] for s in segs if s["swapped"]]})
    print(f"OK: {len(segs)} segment(s) ({encoded} encoded, "
          f"{len(segs) - encoded} cached) -> {args.out.replace(BS, '/')} "
          f"at {w}x{h}/{fps}fps, {actual:.2f}s picture vs {audio_dur:.2f}s "
          f"audio from {plan['audio_master']} (a:{mode}).")

if __name__ == "__main__":
    main()
