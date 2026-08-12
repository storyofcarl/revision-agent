#!/usr/bin/env python3
"""Validate notes.json before Gate 1 — the plan validator.

Usage: python validate_notes.py <notes.json>
Verbose, specific errors so problems are fixable without guesswork.
Exit nonzero on any error; Gate 1 must not proceed until clean.
"""
import json, sys

REQ = ["note_id", "source", "raw_text", "scope", "interpretation",
       "acceptance_criteria", "method", "est_cost", "status", "lineage"]
VAGUE = ("cinematic", "better", "punch", "pop", "feel", "improve", "cooler")

def main():
    doc = json.load(open(sys.argv[1]))
    errs, ids = [], set()
    for n in doc.get("notes", []):
        nid = n.get("note_id", "<missing note_id>")
        if nid in ids:
            errs.append(f"{nid}: duplicate note_id")
        ids.add(nid)
        for k in REQ:
            if k not in n:
                errs.append(f"{nid}: missing field '{k}'")
        if n.get("scope") not in ("foundational", "local"):
            errs.append(f"{nid}: scope must be foundational|local")
        if n.get("method") not in (1, 2, 3, 4):
            errs.append(f"{nid}: method must be 1-4")
        if n.get("scope") == "foundational" and not n.get("assets_touched"):
            errs.append(f"{nid}: foundational note requires assets_touched")
        if n.get("scope") == "local" and not n.get("resolved_shots"):
            errs.append(f"{nid}: local note has no resolved_shots — run "
                        f"resolve_timecodes.py or escalate the ambiguity")
        crits = n.get("acceptance_criteria", [])
        if not crits:
            errs.append(f"{nid}: no acceptance criteria — every note needs at "
                        f"least one forced-choice question")
        for c in crits:
            q = c.get("question", "")
            if not q.endswith("?"):
                errs.append(f"{nid}/{c.get('id')}: criterion is not a question")
            if any(w in q.lower() for w in VAGUE):
                errs.append(f"{nid}/{c.get('id')}: '{q}' is not observable — "
                            f"rewrite as a concrete visual check or take the "
                            f"note to Gate 1 for interpretation choices")
    if errs:
        print(f"INVALID — {len(errs)} problem(s):")
        [print(" -", e) for e in errs]
        sys.exit(1)
    print(f"OK: {len(doc['notes'])} notes valid. Proceed to Gate 1.")

if __name__ == "__main__":
    main()
