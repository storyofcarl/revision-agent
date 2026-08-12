#!/usr/bin/env python3
"""Shared plumbing for revision-agent tools: JSON error exits and per-job
invocation logging. Not a CLI."""
import json, os, sys, time

def fail(code, message, **extra):
    """Exit non-zero with a machine-readable error object on stderr."""
    err = {"error": code, "message": message}
    err.update(extra)
    print(json.dumps(err), file=sys.stderr)
    sys.exit(1)

def log_invocation(job_dir, tool, argv=None, extra=None):
    """Append this invocation to jobs/<id>/logs/tools.jsonl when running
    inside a job; silently no-op otherwise."""
    if not job_dir or not os.path.isdir(job_dir):
        return
    log_dir = os.path.join(job_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "tool": tool, "argv": argv if argv is not None else sys.argv[1:]}
    if extra:
        entry.update(extra)
    with open(os.path.join(log_dir, "tools.jsonl"), "a") as fh:
        fh.write(json.dumps(entry) + "\n")
