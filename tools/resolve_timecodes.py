#!/usr/bin/env python3
"""Resolve note timecodes to shot IDs against the lineup's duration ladder.

Usage: python resolve_timecodes.py <job_dir> <notes.json>
Reads shots.json (standalone) or storyboard-derived shots.json (occ-native
export) with per-shot duration_s in lineup order; writes resolved_shots into
each note in place. Deterministic lookup — no inference.
"""
import json, sys

def tc_to_seconds(tc, fps):
    parts = tc.split(":")
    if len(parts) != 4:
        raise ValueError(f"Timecode '{tc}' must be HH:MM:SS:FF")
    h, m, s, f = (int(p) for p in parts)
    return h * 3600 + m * 60 + s + f / fps

def build_ladder(job_dir):
    with open(f"{job_dir}/shots.json") as fh:
        shots = json.load(fh)
    ladder, t = [], 0.0
    for sh in shots:
        ladder.append((t, t + sh["duration_s"], sh["shot_id"]))
        t += sh["duration_s"]
    return ladder, t

def resolve(ladder, t_in, t_out):
    return [sid for a, b, sid in ladder if a < t_out and b > t_in]

def main():
    job_dir, notes_path = sys.argv[1], sys.argv[2]
    ladder, total = build_ladder(job_dir)
    doc = json.load(open(notes_path))
    problems = []
    for n in doc["notes"]:
        tc = n.get("timecode")
        if not tc:                       # asset/global note — no anchor
            continue
        fps = tc.get("fps_base")
        if not fps:
            problems.append(f"{n['note_id']}: fps_base missing — confirm the "
                            f"note's timecode base with the user; do not assume.")
            continue
        t_in = tc_to_seconds(tc["in"], fps)
        t_out = tc_to_seconds(tc["out"], fps) if tc.get("out") else t_in
        if t_out > total + 0.5:
            problems.append(f"{n['note_id']}: range ends at {t_out:.2f}s but "
                            f"lineup is {total:.2f}s — wrong cut version or wrong fps base?")
            continue
        n["resolved_shots"] = resolve(ladder, t_in, t_out)
        if not n["resolved_shots"]:
            problems.append(f"{n['note_id']}: no shot overlaps {tc['in']}–{tc.get('out')}")
    json.dump(doc, open(notes_path, "w"), indent=2)
    if problems:
        print("RESOLUTION PROBLEMS (escalate to user, do not guess):")
        [print(" -", p) for p in problems]
        sys.exit(1)
    print(f"OK: resolved {sum(1 for n in doc['notes'] if n.get('resolved_shots'))} "
          f"timecoded notes against a {total:.2f}s lineup.")

if __name__ == "__main__":
    main()
