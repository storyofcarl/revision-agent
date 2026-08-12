#!/usr/bin/env python3
"""Render the resubmission changelog from notes.json — never hand-written.

Usage: python build_changelog.py <notes.json> > revision/changelog_roundN.md
Unresolved notes are stated, not hidden.
"""
import json, signal, sys

# stdout is typically piped; exit quietly if the reader closes early
# (SIGPIPE does not exist on Windows — guard the registration)
if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

STATUS_LABEL = {"done": "Addressed", "verified": "Addressed",
                "escalated": "ESCALATED — needs a decision",
                "failed": "NOT RESOLVED"}

def main():
    doc = json.load(open(sys.argv[1]))
    print(f"# Revision round {doc['round']} — change log\n")
    open_items = [n for n in doc["notes"] if n["status"] not in ("done", "verified")]
    print(f"{len(doc['notes'])} notes; {len(doc['notes']) - len(open_items)} addressed, "
          f"{len(open_items)} open.\n")
    for n in doc["notes"]:
        label = STATUS_LABEL.get(n["status"], n["status"].upper())
        shots = ", ".join(n.get("resolved_shots", [])) or "(asset-level)"
        print(f"## {n['note_id']} — {label}")
        print(f"> {n['raw_text']}\n")
        print(f"- What was done: {n['interpretation']} (method {n['method']})")
        print(f"- Shots: {shots}")
        if n["status"] == "escalated":
            print(f"- Needs: accept best version, cut around, or re-scope.")
        print()

if __name__ == "__main__":
    main()
