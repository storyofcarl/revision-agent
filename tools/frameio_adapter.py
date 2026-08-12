#!/usr/bin/env python3
"""frame.io API adapter — DOCUMENTED STUB (spec §2.2). Wiring the real API
later is a fill-in, not a redesign: implement fetch_comments() to return
RawComment objects and intake_normalize.py --source frameio consumes them
unchanged (serialize with [rc.__dict__ for rc in comments] to JSON — that
is exactly the JSON-export shape it already parses).

Until then, use frame.io's manual CSV/JSON export dropped into the job's
intake/ folder.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RawComment:
    """One frame.io comment, flattened to what intake needs.

    Fields (all strings unless noted):
      comment_id    frame.io's comment id — becomes source.ref
      asset_id      the reviewed asset the comment sits on
      author        display name of the commenter
      text          verbatim comment text — becomes raw_text
      timecode_in   comment timecode, any of MM:SS / HH:MM:SS / HH:MM:SS:FF
      timecode_out  range end, or None for point comments
      fps_base      int fps of the timecode base, or None if unknown
      parent_id     parent comment_id for replies (replies merge into
                    their parent's note at intake), or None
      created_at    ISO-8601 timestamp
    """
    comment_id: str
    asset_id: str
    author: str
    text: str
    timecode_in: Optional[str] = None
    timecode_out: Optional[str] = None
    fps_base: Optional[int] = None
    parent_id: Optional[str] = None
    created_at: Optional[str] = None


def fetch_comments(asset_id: str) -> "list[RawComment]":
    """Fetch all comments (including replies) for a frame.io asset.

    NOT IMPLEMENTED in v1 — the manual-export path is the supported
    intake. When implemented: authenticate with FRAMEIO_TOKEN from the
    repo .env, page through the comments endpoint for asset_id, and
    return one RawComment per comment in thread order (parents before
    replies).
    """
    raise NotImplementedError(
        "frame.io API intake is a documented stub in v1 — export comments "
        "manually (CSV/JSON) into the job's intake/ folder and run "
        "intake_normalize.py --source frameio on the export.")


if __name__ == "__main__":
    import sys
    print(__doc__)
    sys.exit(0)
