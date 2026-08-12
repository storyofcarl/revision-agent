#!/usr/bin/env python3
"""Write est_cost into each note using the CreationCanvas token formulas.

Usage: python estimate_costs.py <notes.json>
Requires shots.json durations already resolved (run resolve_timecodes.py
first). Verify RATES against creationcanvas.com/calc when prices change.
"""
import json, sys

# $/M tokens, verified against BytePlus published examples via
# creationcanvas.com/calc (2026-07). Re-verify on price changes.
RATES = {  # (no_video, with_video)
    "pro_1080p": (7.7, 4.7), "pro_720p": (7.0, 4.3), "pro_480p": (7.0, 4.3),
    "fast_720p": (5.6, 3.3), "mini_480p": (3.5, 2.1),
}
# pro_480p dims are 21:9 (the scope-format working res added for the
# Spookley pilot); Pro bills 720p and 480p at the same $/M rate.
DIMS = {"pro_1080p": (1920, 1080), "pro_720p": (1280, 720),
        "pro_480p": (1120, 480),
        "fast_720p": (1280, 720), "mini_480p": (864, 480)}
FPS = 24
# Seed expected-attempt ratios per method; the video-qc ledger overrides
# these once it holds >=3 jobs of real repair data (routing-and-costs.md).
SEED_ATTEMPTS = {1: 3, 2: 3, 3: 4, 4: 4}
MIN_GEN_S = 4.0          # Seedance generation floor
WHITE_PASS_VARIANT = "mini_480p"

def per_attempt(variant, seconds, v2v):
    """Video-reference cost is ADDITIVE: output seconds bill at the regular
    (no-video) rate, and input video seconds (~1:1 with output) bill at the
    with-video rate on top."""
    w, h = DIMS[variant]
    tok_s = (w * h * FPS) / 1024          # tokens per second of video
    no_vid, with_vid = RATES[variant]     # $ per 1M tokens
    usd = (tok_s / 1_000_000) * no_vid * seconds
    if v2v:
        usd += (tok_s / 1_000_000) * with_vid * seconds   # input video, additive
    return usd

def main():
    notes_path = sys.argv[1]
    doc = json.load(open(notes_path))
    variant = {"480p": "pro_480p", "720p": "pro_720p",
               "1080p": "pro_1080p"}[doc["working_res"]]
    shot_dur = {}
    for sh in json.load(open(f"{doc['job_dir']}/shots.json")):
        shot_dur[sh["shot_id"]] = sh["duration_s"]
    total = 0.0
    for n in doc["notes"]:
        if n.get("editorial"):
            # editorial execution (retime, reposition, cut-around): no
            # generation, no spend — priced zero, stated in the table
            n["est_cost"] = {"per_attempt_usd": 0.0, "expected_attempts": 0,
                             "expected_usd": 0.0}
            continue
        secs = max(sum(shot_dur.get(s, 0) for s in n.get("resolved_shots", [])), MIN_GEN_S)
        m = n["method"]
        v2v = m in (1, 3)
        pa = per_attempt(variant, secs, v2v)
        if m == 1:
            pa += per_attempt(WHITE_PASS_VARIANT, secs, True)  # white pass, once
        attempts = SEED_ATTEMPTS[m]
        n["est_cost"] = {"per_attempt_usd": round(pa, 2),
                         "expected_attempts": attempts,
                         "expected_usd": round(pa * attempts, 2)}
        total += n["est_cost"]["expected_usd"]
    doc.setdefault("totals", {})["expected_usd"] = round(total, 2)
    json.dump(doc, open(notes_path, "w"), indent=2)
    print(f"OK: project expected cost ${total:.2f} at {doc['working_res']} "
          f"({len(doc['notes'])} notes). Present per-note figures at Gate 1.")

if __name__ == "__main__":
    main()
