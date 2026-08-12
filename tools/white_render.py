#!/usr/bin/env python3
"""Convert shot clip(s) to white textureless motion-reference renders
(Method 1 pre-step) via Seedance Mini V2V, returning hosted URLs.

Usage:
  python white_render.py --clips <paths...> [--manifest <shots.json>]
                         [--out-dir <dir>] [--job-dir <dir>] [--dry-run]

Contract:
  - Prompt: the white-pass conversion prompt from
    references/prompt-patterns.md, verbatim. Seedance Mini, 480p, V2V.
  - Sub-4s bundling (spec §5.3): clips under Seedance's 4s floor are
    bundled with adjacent shots from the manifest to reach the minimum.
    Partner preference: a neighbor already receiving a note (i.e. also in
    --clips), then any in-scene neighbor; never across a scene boundary
    when an in-scene neighbor exists. A sub-4s clip with no available
    partner is an error, not a guess. Bundles are recorded to
    <out-dir>/white_bundles.json (lineage: shots, per-shot offsets) so
    verification scopes the whole bundle as changed.
  - Manifest: shots.json entries need shot_id + duration_s in lineup
    order; scene comes from a "scene" field or the SQ###-SC### prefix of
    shot_id. Without --manifest, clips must each be >= 4s (probed via
    ffprobe) — bundling needs adjacency, which only the manifest defines.
  - Neighbor clips are resolved as <shot_id>.mp4 in the same directory as
    the target clip.
  - Output: renders in <out-dir> (default <job-dir>/revision/white),
    each also hosted via Supabase; stdout JSON lists per-render
    {render, shots, spans, local, hosted_url}.
  - --dry-run: prints the full plan (bundles, durations, estimated cost)
    with zero network calls and zero spend.
"""
import argparse, json, math, os, re, subprocess, sys, tempfile

import ark_client as ark
import supabase_upload
from _common import fail, log_invocation

MIN_GEN_S = 4.0
MAX_BUNDLE_S = 15.0     # occ packing ceiling / Seedance task max
# mini 480p V2V: tokens/s=(864*480*24)/1024; $3.5/M output + $2.1/M input video
EST_USD_PER_S = round(((864 * 480 * 24) / 1024 / 1e6) * (3.5 + 2.1), 4)

WHITE_PROMPT = (
    "Strictly edit <Video_1>: render the entire video as a plain white, "
    "textureless, unlit-clay 3D version of itself. Identical geometry, "
    "identical motion, identical camera movement, identical timing. Flat "
    "white surfaces, neutral grey background, no textures, no color, no "
    "lighting mood. No music. No text/logos.")

def probe_duration(clip):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", clip],
        capture_output=True, text=True, check=True)
    return float(json.loads(out.stdout)["format"]["duration"])

def scene_of(shot):
    if isinstance(shot.get("scene"), str):
        return shot["scene"]
    m = re.match(r"(SQ\d+-SC\d+)", shot["shot_id"])
    return m.group(1) if m else "_unknown"

def plan_bundles(targets, manifest):
    """targets: set of shot_ids receiving a white pass. Returns a list of
    plans: {"shots": [ids in lineup order], "duration_s": float}."""
    order = [s["shot_id"] for s in manifest]
    dur = {s["shot_id"]: float(s["duration_s"]) for s in manifest}
    scene = {s["shot_id"]: scene_of(s) for s in manifest}
    idx = {sid: i for i, sid in enumerate(order)}
    for t in targets:
        if t not in idx:
            fail("unknown_shot", f"{t} is not in the manifest")
    consumed, plans = set(), []
    for t in sorted(targets, key=lambda s: idx[s]):
        if t in consumed:
            continue
        run = [t]
        total = dur[t]
        while total < MIN_GEN_S:
            lo, hi = idx[run[0]], idx[run[-1]]
            cands = []
            for j, side in ((lo - 1, "prev"), (hi + 1, "next")):
                if 0 <= j < len(order) and order[j] not in consumed:
                    cands.append(order[j])
            if not cands:
                fail("no_bundle_partner",
                     f"{t} is {dur[t]:.2f}s (< {MIN_GEN_S}s floor) and has no "
                     f"available bundle partner in the manifest")
            def rank(c):
                # prefer: also-targeted, then in-scene; never cross-scene
                # when an in-scene candidate exists (handled below)
                return (0 if c in targets else 1,
                        0 if scene[c] == scene[t] else 1)
            in_scene = [c for c in cands if scene[c] == scene[t]]
            pool = in_scene if in_scene else cands
            best = sorted(pool, key=rank)[0]
            if total + dur[best] > MAX_BUNDLE_S:
                fail("bundle_over_cap",
                     f"{t}: bundling {best} would exceed the {MAX_BUNDLE_S}s "
                     f"task ceiling — re-plan manually")
            if idx[best] < idx[run[0]]:
                run.insert(0, best)
            else:
                run.append(best)
            total += dur[best]
        consumed.update(run)
        plans.append({"shots": run, "duration_s": round(total, 3)})
    return plans

def concat_clips(paths, out_path):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        for p in paths:
            fh.write(f"file '{os.path.abspath(p).replace(chr(92), '/')}'\n")
        lst = fh.name
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe",
                        "0", "-i", lst, "-c", "copy", out_path], check=True)
    finally:
        os.unlink(lst)

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clips", nargs="+", required=True,
                    help="shot clips to white-render (named <shot_id>.mp4)")
    ap.add_argument("--manifest", help="shots.json (lineup order, duration_s; "
                                       "required for sub-4s bundling)")
    ap.add_argument("--out-dir", help="default <job-dir>/revision/white")
    ap.add_argument("--job-dir", help="job directory (outputs, cache, logging)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan; nothing uploaded, submitted, or spent")
    args = ap.parse_args()
    ark.load_env()
    for c in args.clips:
        if not os.path.exists(c):
            fail("no_such_file", f"{c} does not exist")
    out_dir = args.out_dir or (os.path.join(args.job_dir, "revision", "white")
                               if args.job_dir else "white")
    cache_path = (os.path.join(args.job_dir, "revision", "uploads.json")
                  if args.job_dir else "uploads.json")
    clip_dir = os.path.dirname(os.path.abspath(args.clips[0]))
    sid_of = lambda p: os.path.splitext(os.path.basename(p))[0]

    if args.manifest:
        manifest = json.load(open(args.manifest))
        dur = {s["shot_id"]: float(s["duration_s"]) for s in manifest}
        plans = plan_bundles({sid_of(c) for c in args.clips}, manifest)
    else:
        manifest = None
        plans = []
        for c in args.clips:
            d = probe_duration(c)
            if d < MIN_GEN_S:
                fail("no_bundle_partner",
                     f"{c} is {d:.2f}s (< {MIN_GEN_S}s floor) — bundling needs "
                     f"--manifest for adjacency")
            plans.append({"shots": [sid_of(c)], "duration_s": round(d, 3)})
        dur = {p["shots"][0]: p["duration_s"] for p in plans}

    renders = []
    for p in plans:
        name = p["shots"][0] + ("_bundle" if len(p["shots"]) > 1 else "")
        spans, t = [], 0.0
        for sid in p["shots"]:
            spans.append({"shot": sid, "offset_s": round(t, 3),
                          "duration_s": dur[sid]})
            t += dur[sid]
        renders.append({"render": name, "shots": p["shots"], "spans": spans,
                        "duration_s": p["duration_s"],
                        "est_usd": round(p["duration_s"] * EST_USD_PER_S, 2)})

    if args.dry_run:
        print(json.dumps({"plan": renders,
                          "est_total_usd": round(sum(r["est_usd"] for r in renders), 2),
                          "dry_run": True}, indent=2))
        return

    os.makedirs(out_dir, exist_ok=True)
    for r in renders:
        if len(r["shots"]) == 1:
            src = os.path.join(clip_dir, r["shots"][0] + ".mp4")
        else:
            src = os.path.join(out_dir, r["render"] + "_src.mp4")
            parts = [os.path.join(clip_dir, sid + ".mp4") for sid in r["shots"]]
            for pp in parts:
                if not os.path.exists(pp):
                    fail("no_such_file", f"bundle partner clip {pp} does not exist")
            concat_clips(parts, src)
        src_url = ark.upload_hosted(src, cache_path)
        content = ark.build_content(WHITE_PROMPT, ref_video_url=src_url)
        tid = ark.submit_video(ark.ENDPOINTS["seedance_mini"], content,
                               ratio="16:9", resolution="480p",
                               duration=math.ceil(r["duration_s"]),
                               generate_audio=False)
        print(f"{r['render']}: task {tid} submitted ({r['duration_s']}s)")
        url = ark.poll_video(tid)
        local = os.path.join(out_dir, r["render"] + ".mp4")
        ark.download(url, local)
        r["local"] = local.replace("\\", "/")
        r["hosted_url"] = ark.upload_hosted(local, cache_path)
        print(f"{r['render']}: done -> {r['hosted_url']}")

    sidecar = os.path.join(out_dir, "white_bundles.json")
    existing = json.load(open(sidecar)) if os.path.exists(sidecar) else []
    existing.extend(renders)
    json.dump(existing, open(sidecar, "w"), indent=2)
    log_invocation(args.job_dir, "white_render",
                   extra={"renders": [r["render"] for r in renders]})
    print(json.dumps({"renders": renders, "sidecar": sidecar.replace("\\", "/")},
                     indent=2))

if __name__ == "__main__":
    main()
