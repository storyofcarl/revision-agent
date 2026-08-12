#!/usr/bin/env python3
"""Normalize a notes source into candidate Note objects for the operator
to complete.

Usage:
  python intake_normalize.py --source {frameio|email|sheet} <input>
                             [--out <path>] [--job-dir <dir>]
                             [--fps <base>] [--start-id N]

Parses only what is deterministic — timecodes, verbatim text, author,
source row/comment id. The judgment fields (interpretation,
acceptance_criteria, scope, method) are left null/empty for the operator,
per agent/references/intake-guide.md; schema per
agent/references/note-schema.md.

Sources:
  frameio  CSV export or JSON export (list of comment objects — the shape
           frameio_adapter.fetch_comments will return). Reply threads
           (parent_id) merge into their parent note's raw_text.
  email    .txt or .eml. Body splits into candidate notes on blank lines
           and bullet/numbered list items; timecodes are regex-extracted.
  sheet    .csv (built-in) or .xlsx (requires openpyxl). Columns mapped by
           header name; ambiguous mappings are an error to resolve with
           the user — never guessed.

Timecodes normalize to HH:MM:SS:FF. fps_base is null unless --fps is
given or the source states it — resolve_timecodes.py refuses null fps, so
the operator must confirm the base before Gate 1.

Output: {"notes": [...candidates...]} to stdout or --out. Exit 0 on
success; non-zero with a JSON error object on stderr otherwise.
"""
import argparse, csv, io, json, os, re, sys

from _common import fail, log_invocation

TC = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?::\d{2})?"
TC_RANGE = re.compile(rf"({TC})\s*(?:-|–|—|to)\s*({TC})")
TC_SINGLE = re.compile(rf"(?:^|[\s@(])({TC})(?=$|[\s,.)\];])")

HDR_MAP = {  # canonical field -> header aliases (lowercased)
    "timecode": ("timecode", "tc", "time", "timestamp"),
    "text": ("note", "notes", "text", "comment", "description", "feedback"),
    "shot": ("shot", "shot_id", "shot id"),
    "author": ("author", "requested_by", "requested by", "name", "commenter", "from"),
    "ref": ("id", "row", "ref", "comment_id"),
}

def norm_tc(tc):
    """Normalize '1:02', '01:02:12', '00:01:02:12' -> HH:MM:SS:FF."""
    parts = tc.split(":")
    if len(parts) == 2:                      # MM:SS
        h, m, s, f = 0, int(parts[0]), int(parts[1]), 0
    elif len(parts) == 3:                    # HH:MM:SS
        h, m, s, f = int(parts[0]), int(parts[1]), int(parts[2]), 0
    elif len(parts) == 4:                    # HH:MM:SS:FF
        h, m, s, f = (int(p) for p in parts)
    else:
        return None
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"

def find_timecode(text, fps):
    m = TC_RANGE.search(text)
    if m:
        return {"in": norm_tc(m.group(1)), "out": norm_tc(m.group(2)),
                "fps_base": fps}
    m = TC_SINGLE.search(text)
    if m:
        return {"in": norm_tc(m.group(1)), "out": None, "fps_base": fps}
    return None

def note_obj(nid, src_type, ref, author, raw, timecode, shot=None):
    return {
        "note_id": f"N-{nid:03d}",
        "source": {"type": src_type, "ref": ref, "author": author},
        "raw_text": raw.strip(),
        "timecode": timecode,
        "resolved_shots": [shot] if shot else [],
        "scope": None,
        "interpretation": None,
        "acceptance_criteria": [],
        "region": None,
        "method": None,
        "assets_touched": [],
        "est_cost": None,
        "status": "pending",
        "lineage": {"parent_clip": None, "revision_cycle": 1,
                    "approved_stills": [], "white_pass": None,
                    "candidates": []},
    }

# ---------------- frame.io -------------------------------------------------
def parse_frameio(path, fps, start_id):
    if path.lower().endswith(".json"):
        rows = json.load(open(path, encoding="utf-8-sig"))
        if not isinstance(rows, list):
            fail("bad_format", "frame.io JSON export must be a list of comments")
        by_id, order = {}, []
        for c in rows:  # RawComment shape — see frameio_adapter.py
            pid = c.get("parent_id")
            if pid and pid in by_id:   # reply thread: merge into parent
                by_id[pid]["text"] += "\n[reply] " + (c.get("text") or "")
                continue
            c = dict(c)
            by_id[c.get("comment_id") or f"row{len(order)}"] = c
            order.append(c)
        notes = []
        for i, c in enumerate(order):
            tc = None
            if c.get("timecode_in"):
                tc = {"in": norm_tc(c["timecode_in"]),
                      "out": norm_tc(c["timecode_out"]) if c.get("timecode_out") else None,
                      "fps_base": c.get("fps_base") or fps}
            notes.append(note_obj(start_id + i, "frameio",
                                  c.get("comment_id"), c.get("author"),
                                  c.get("text") or "", tc))
        return notes
    return parse_sheet(path, fps, start_id, src_type="frameio")

# ---------------- email ----------------------------------------------------
def parse_email(path, fps, start_id):
    author = None
    if path.lower().endswith(".eml"):
        import email, email.policy
        msg = email.message_from_binary_file(open(path, "rb"),
                                             policy=email.policy.default)
        author = str(msg.get("From", "")) or None
        body = msg.get_body(preferencelist=("plain",))
        text = body.get_content() if body else ""
    else:
        text = open(path, encoding="utf-8-sig").read()
        m = re.search(r"^From:\s*(.+)$", text, re.M | re.I)
        if m:
            author = m.group(1).strip()
    # strip common header/quote noise, then split into candidate chunks
    lines = [l for l in text.splitlines()
             if not re.match(r"^(from|to|cc|subject|date|sent):", l, re.I)]
    chunks, cur = [], []
    for l in lines:
        bullet = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", l)
        if (not l.strip() or bullet) and cur:
            chunks.append("\n".join(cur).strip())
            cur = []
        if l.strip():
            cur.append(re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", l))
    if cur:
        chunks.append("\n".join(cur).strip())
    chunks = [c for c in chunks if c]
    return [note_obj(start_id + i, "email", f"chunk{i+1}", author, c,
                     find_timecode(c, fps))
            for i, c in enumerate(chunks)]

# ---------------- spreadsheet ----------------------------------------------
def _rows_from_xlsx(path):
    try:
        import openpyxl
    except ImportError:
        fail("missing_dep", ".xlsx input requires openpyxl "
                            "(pip install openpyxl) — or export to .csv")
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    return [["" if v is None else str(v) for v in row]
            for row in ws.iter_rows(values_only=True)]

def parse_sheet(path, fps, start_id, src_type="spreadsheet"):
    if path.lower().endswith(".xlsx"):
        rows = _rows_from_xlsx(path)
    else:
        rows = list(csv.reader(open(path, encoding="utf-8-sig", newline="")))
    if not rows:
        fail("empty", f"{path} has no rows")
    header = [h.strip().lower() for h in rows[0]]
    cols = {}
    for field, aliases in HDR_MAP.items():
        hits = [i for i, h in enumerate(header) if h in aliases]
        if len(hits) > 1:
            fail("ambiguous_mapping",
                 f"multiple columns match '{field}': "
                 f"{[header[i] for i in hits]} — confirm the mapping with "
                 f"the user; never guess", headers=header)
        if hits:
            cols[field] = hits[0]
    if "text" not in cols:
        fail("ambiguous_mapping",
             f"no column matches a note-text header {HDR_MAP['text']} — "
             f"confirm the mapping with the user", headers=header)
    notes = []
    for i, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        get = lambda f: (row[cols[f]].strip() if f in cols and
                         cols[f] < len(row) else None)
        raw = get("text")
        if not raw:
            continue
        tc = None
        tc_cell = get("timecode")
        if tc_cell:
            tc = find_timecode(tc_cell, fps) or (
                {"in": norm_tc(tc_cell), "out": None, "fps_base": fps}
                if norm_tc(tc_cell) else None)
        else:
            tc = find_timecode(raw, fps)
        notes.append(note_obj(start_id + len(notes), src_type,
                              get("ref") or f"row{i}", get("author"), raw,
                              tc, shot=get("shot")))
    return notes

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True,
                    choices=["frameio", "email", "sheet"])
    ap.add_argument("input", help="path to the source file")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    ap.add_argument("--job-dir", help="job directory (logging)")
    ap.add_argument("--fps", type=int, default=None,
                    help="timecode fps base if known (else left null for "
                         "the operator to confirm)")
    ap.add_argument("--start-id", type=int, default=1,
                    help="first note number (default 1)")
    args = ap.parse_args()
    if not os.path.exists(args.input):
        fail("no_such_file", f"{args.input} does not exist")
    parser = {"frameio": parse_frameio, "email": parse_email,
              "sheet": parse_sheet}[args.source]
    notes = parser(args.input, args.fps, args.start_id)
    if not notes:
        fail("empty", f"no candidate notes found in {args.input}")
    doc = {"notes": notes}
    out_text = json.dumps(doc, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        open(args.out, "w").write(out_text)
        print(f"OK: {len(notes)} candidate notes -> {args.out}")
    else:
        print(out_text)
    log_invocation(args.job_dir, "intake_normalize",
                   extra={"source": args.source, "count": len(notes)})

if __name__ == "__main__":
    main()
