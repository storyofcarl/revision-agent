#!/usr/bin/env python3
"""Launcher for a revision round: scaffold or resume the job directory,
record the invocation in state.json, print the operator preamble the
Claude Code session executes against. The session is the agent; this
launcher just guarantees a well-formed starting state (spec §2.1).

Usage:
  python tools/revise.py <job-id> --notes <path>
                         [--occ-project <path>] [--config k=v ...]
                         [--plan-only]

Behavior:
  - jobs/<job-id>/ is created per spec §3 if absent, resumed if present.
    Resuming never overwrites anything — it appends the invocation and
    points the operator at the recorded position in state.json.
  - --notes copies the source file into intake/ (immutable, as received).
    Repeat invocations with new notes files accumulate; nothing is
    replaced.
  - Mode resolution: --occ-project (or job.yaml's occ_project) pointing at
    a directory containing project.yaml + storyboard.md => occ-native;
    otherwise standalone. An occ path that fails validation is an error,
    not a silent fallback.
  - --config key=value overrides job.yaml fields at scaffold time (e.g.
    --config budget_ceiling_usd=25 gates.stills=agent working_res=720p).
  - Gate defaults are conservative (all human) per spec §5.2.

Exit 0 on success with the preamble on stdout; non-zero with a JSON error
object on stderr otherwise.
"""
import argparse, json, os, re, shutil, sys, time

from _common import fail

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

JOB_SUBDIRS = ["intake", "revision", "refs/_prior", "clips",
               "stills/extracted", "stills/edited", "stills/approved",
               "candidates", "verified", "qc", "out", "logs"]

JOB_YAML_TEMPLATE = """\
# Revision Agent job config — spec §3. Edit before Gate 1 if needed.
job_id: {job_id}
created: {created}
mode: {mode}                # standalone | occ-native (resolved by revise.py)
occ_project: {occ_project}  # external occ install path, or null
working_res: 720p           # never above the lineup's current res
budget_ceiling_usd: 50.0    # absolute — nothing generates past it (spec §5.4)
max_concurrent: 3           # set from quota-probe.json when measured
qc_tier: broadcast          # passed through to video-qc --tier

# Delegation dial (spec §5.2): human | agent per gate. Gate 4
# (verification) is machine by definition and never delegable to the
# generating context — the generator never grades itself.
gates:
  lock: human          # Gate 1 — approve the notes table
  foundation: human    # Gate 2 — approve mutated canonical assets
  stills: human        # Gate 3 — approve edited stills (last cheap gate)
"""

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def resolve_mode(occ_project):
    if not occ_project:
        return "standalone"
    if not os.path.isdir(occ_project):
        fail("bad_occ_path", f"--occ-project {occ_project} is not a directory")
    missing = [f for f in ("project.yaml", "storyboard.md")
               if not os.path.exists(os.path.join(occ_project, f))]
    if missing:
        fail("bad_occ_project",
             f"{occ_project} lacks {', '.join(missing)} — not a valid occ "
             f"project; fix the path or omit --occ-project for standalone")
    return "occ-native"

def apply_overrides(yaml_text, overrides):
    """Apply k=v overrides to the flat template (dotted keys hit the gates
    block). String-level on purpose: the template is the schema."""
    for kv in overrides:
        if "=" not in kv:
            fail("bad_config", f"--config expects key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        if k.startswith("gates."):
            gk = k.split(".", 1)[1]
            if gk not in ("lock", "foundation", "stills"):
                fail("bad_config", f"unknown gate {gk!r}")
            if v not in ("human", "agent"):
                fail("bad_config", f"gate value must be human|agent, got {v!r}")
            yaml_text = re.sub(rf"^(  {gk}: )\w+", rf"\g<1>{v}", yaml_text,
                               flags=re.M)
        else:
            pat = rf"^({k}: )\S+"
            if not re.search(pat, yaml_text, flags=re.M):
                fail("bad_config", f"unknown job.yaml key {k!r}")
            yaml_text = re.sub(pat, rf"\g<1>{v}", yaml_text, flags=re.M)
    return yaml_text

def ceiling_of(yaml_text):
    """job.yaml is the budget authority (spec §5.4.2) — seed state.json from
    the text actually written, so --config budget_ceiling_usd=N is honoured
    and the two files never disagree."""
    m = re.search(r"^budget_ceiling_usd: ([\d.]+)", yaml_text, flags=re.M)
    return float(m.group(1)) if m else 0.0

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_id", help="job identifier (directory name under jobs/)")
    ap.add_argument("--notes", help="notes source file (copied into intake/)")
    ap.add_argument("--occ-project", help="path to an external occ project "
                                          "(=> occ-native mode)")
    ap.add_argument("--config", nargs="*", default=[],
                    help="job.yaml overrides as key=value (scaffold time only)")
    ap.add_argument("--plan-only", action="store_true",
                    help="record that this round is plan-only: intake through "
                         "cost estimate with zero spend (Brief 02 feature)")
    args = ap.parse_args()

    job_dir = os.path.join(REPO, "jobs", args.job_id)
    state_path = os.path.join(job_dir, "state.json")
    fresh = not os.path.exists(state_path)

    if fresh:
        mode = resolve_mode(args.occ_project)
        for d in JOB_SUBDIRS:
            os.makedirs(os.path.join(job_dir, d), exist_ok=True)
        yaml_text = JOB_YAML_TEMPLATE.format(
            job_id=args.job_id, created=now(), mode=mode,
            occ_project=args.occ_project or "null")
        yaml_text = apply_overrides(yaml_text, args.config)
        open(os.path.join(job_dir, "job.yaml"), "w").write(yaml_text)
        state = {"job_id": args.job_id, "created": now(), "mode": mode,
                 "position": {"step": 1, "name": "INTAKE"},
                 "steps": [{"n": i + 1, "name": nm, "status": "pending"}
                           for i, nm in enumerate(
                               ["INTAKE", "LOCK", "FOUNDATION", "STILLS",
                                "GENERATE", "VERIFY", "REPAIR", "REASSEMBLE",
                                "RESUBMIT"])],
                 "invocations": [], "ledger": [], "escalations": [],
                 "budget": {"ceiling_usd": ceiling_of(yaml_text),
                            "actual_usd": 0.0}}
    else:
        if args.config:
            fail("config_after_scaffold",
                 "--config only applies at scaffold time; edit job.yaml "
                 "directly to change a live job")
        state = json.load(open(state_path))
        mode = state["mode"]

    if args.notes:
        if not os.path.exists(args.notes):
            fail("no_such_file", f"--notes {args.notes} does not exist")
        dest = os.path.join(job_dir, "intake", os.path.basename(args.notes))
        if os.path.exists(dest) and not os.path.samefile(args.notes, dest):
            base, ext = os.path.splitext(os.path.basename(args.notes))
            dest = os.path.join(job_dir, "intake", f"{base}_{now().replace(':', '')}{ext}")
        if not os.path.exists(dest):
            shutil.copyfile(args.notes, dest)
    elif fresh:
        fail("no_notes", "a fresh round needs --notes <path-or-paste-file>")

    state["invocations"].append({"ts": now(), "argv": sys.argv[1:],
                                 "plan_only": args.plan_only})
    json.dump(state, open(state_path, "w"), indent=2)

    pos = state["position"]
    print(f"""\
=== Revision Agent — round {'scaffolded' if fresh else 'resumed'} ===
job:      {job_dir}
mode:     {mode}
position: step {pos['step']} ({pos['name']})
plan_only: {args.plan_only}

Operator: read agent/OPERATOR.md and execute this round from the recorded
position. state.json is the ledger — never repeat a completed step, never
repeat spend. The revision-pipeline skill's operating contract governs.""")

if __name__ == "__main__":
    main()
