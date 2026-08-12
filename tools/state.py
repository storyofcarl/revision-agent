#!/usr/bin/env python3
"""The job state machine: state.json is the round's ledger and the only
thing a resumed session trusts. Every tool invocation in a round goes
through this module's runner so command, duration, exit code and cost are
recorded exactly once, in state.json and logs/tools.jsonl (spec §3, brief
02 §4). Importable as a library by the operator and by other tools; also a
CLI so a shell step is logged identically to a scripted one.

Usage:
  python state.py <job_dir> run [--cost-usd X] [--capture] -- <tool.py> [args...]
  python state.py <job_dir> show [--tail N]

`run` executes the command, appends the ledger entry, adds --cost-usd to
budget.actual_usd, and exits with the child's exit code (child stdout/err
stream through unless --capture). A nonzero child is recorded, never
raised over: the operator classifies the failure (spec §5.4.5).

`show` prints position, budget, batches, escalations and the ledger tail —
the first thing to run when picking a round back up.

Library API (the operator codes against this):
  load(job_dir) / save(job_dir, state)
  run_tool(job_dir, argv, cost_usd=0.0)     -> CompletedProcess (never raises)
  start_step(job_dir, n)                    -> False (warn) if already done
  complete_step(job_dir, n, summary)        -> completing twice warns, no-ops
  new_round(job_dir, summary="")            -> archive steps, reset to step 1
  record_gate(job_dir, gate, decider, outcome, evidence, rationale)
  escalate(job_dir, trigger, note_id, blocked_on, evidence, options)
  check_budget(job_dir, projected_usd)      -> (ok, ceiling, actual)
  batch_update(job_dir, batch_id, note_id, task_id, status)
  batch_state(job_dir, batch_id)            -> {note_id: {task_id, status, ts}}

Idempotency contract: start_step/complete_step on a completed step are
warning no-ops, so a re-run of a finished step costs nothing and adds no
ledger noise. Partial batch state is first-class — batch_update records
each note's task_id and status (submitted|generated|failed|verified|...)
as it moves, so an interrupted round resumes mid-batch instead of
resubmitting. check_budget is called BEFORE every paid submission, never
after; the ceiling in job.yaml is absolute (spec §5.4.2).
"""
import argparse, json, os, re, subprocess, sys, time

from _common import fail, log_invocation

TOOLS = os.path.dirname(os.path.abspath(__file__))

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def warn(msg):
    print(f"WARNING: {msg}", file=sys.stderr)

def _path(job_dir):
    p = os.path.join(job_dir, "state.json")
    if not os.path.exists(p):
        fail("no_such_job", f"{p} not found — scaffold the round with revise.py first")
    return p

def load(job_dir):
    """state.json as scaffolded by revise.py, with the Brief-02 containers
    (gates, batches) defaulted in so callers never guard for them."""
    st = json.load(open(_path(job_dir)))
    for k, default in (("invocations", []), ("ledger", []), ("escalations", []),
                       ("gates", []), ("batches", {}), ("ledger_from", 0),
                       ("budget", {"ceiling_usd": 0.0, "actual_usd": 0.0})):
        st.setdefault(k, default)
    return st

def save(job_dir, state):
    """Atomic: a kill mid-write leaves the previous state intact, which is
    the whole point of a resumable ledger."""
    p = _path(job_dir)
    tmp = p + ".tmp"
    json.dump(state, open(tmp, "w"), indent=2)
    os.replace(tmp, p)
    return state

def job_config(job_dir):
    """job.yaml is flat template text (revise.py owns the schema) — parse it
    line-wise rather than adding a YAML dependency. Returns scalars plus a
    nested 'gates' dict; values stay strings except the numeric ones."""
    path = os.path.join(job_dir, "job.yaml")
    cfg = {"gates": {}}
    if not os.path.exists(path):
        return cfg
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.split("#", 1)[0].rstrip()
        m = re.match(r"^(\S+):\s*(\S.*)?$", line)
        if m and m.group(2):
            cfg[m.group(1)] = m.group(2).strip()
            continue
        m = re.match(r"^  (\S+):\s*(\S+)$", line)
        if m:
            cfg["gates"][m.group(1)] = m.group(2)
    return cfg

# ---------------- the single runner ---------------------------------------

def _argv_for_exec(argv):
    """A bare tool name (or path) ending in .py runs under this interpreter,
    resolved against tools/ — so callers write ['diff_gate.py', ...] and
    Windows never has to care about shebangs."""
    if argv and str(argv[0]).endswith(".py"):
        p = argv[0] if os.path.exists(argv[0]) else os.path.join(TOOLS, os.path.basename(argv[0]))
        return [sys.executable, p, *[str(a) for a in argv[1:]]]
    return [str(a) for a in argv]

def run_tool(job_dir, argv, cost_usd=0.0, capture=True, cwd=None):
    """THE runner. Executes argv, records {ts, argv, exit, duration_s,
    cost_usd} in state['ledger'] and logs/tools.jsonl, and adds cost_usd to
    budget.actual_usd. Never raises on a nonzero exit — the caller reads
    .returncode and decides (retry, repair, escalate). capture=False
    streams the child's output live (batch polling runs for minutes)."""
    argv = list(argv)
    if not argv:
        fail("empty_argv", "run_tool needs a command to run")
    t0 = time.monotonic()
    cp = subprocess.run(_argv_for_exec(argv), capture_output=capture, text=True,
                        cwd=cwd or os.path.dirname(TOOLS))
    entry = {"ts": now(), "argv": argv, "exit": cp.returncode,
             "duration_s": round(time.monotonic() - t0, 3),
             "cost_usd": round(float(cost_usd), 4)}
    st = load(job_dir)
    st["ledger"].append(entry)
    st["budget"]["actual_usd"] = round(st["budget"].get("actual_usd", 0.0)
                                       + float(cost_usd), 4)
    save(job_dir, st)
    log_invocation(job_dir, os.path.basename(str(argv[0])), argv=argv[1:],
                   extra={k: entry[k] for k in ("exit", "duration_s", "cost_usd")})
    return cp

# ---------------- steps ----------------------------------------------------

def _step(state, n):
    for s in state["steps"]:
        if s["n"] == n:
            return s
    fail("no_such_step", f"step {n} is not in the 9-step ledger")

def start_step(job_dir, n):
    """Idempotent entry: False (with a warning) if step n already completed,
    so a resumed session cannot repeat work or spend. Starting a step also
    un-parks a round that was blocked on an escalation."""
    st = load(job_dir)
    s = _step(st, n)
    if s["status"] == "completed":
        warn(f"step {n} ({s['name']}) already completed — no-op. "
             f"Resume from position {st['position']['step']}.")
        return False
    s["status"] = "in_progress"
    s["started"] = now()
    st["position"] = {"step": n, "name": s["name"]}
    save(job_dir, st)
    return True

def complete_step(job_dir, n, summary):
    """Mark step n done with a one-line summary and advance position to the
    first step still outstanding. Completing a completed step warns and
    no-ops (the summary is not overwritten)."""
    st = load(job_dir)
    s = _step(st, n)
    if s["status"] == "completed":
        warn(f"step {n} ({s['name']}) already completed — no-op, summary kept.")
        return False
    s["status"] = "completed"
    s["summary"] = summary
    s["completed"] = now()
    nxt = next((x for x in st["steps"] if x["status"] != "completed"), s)
    pos = {"step": nxt["n"], "name": nxt["name"]}
    if st["position"].get("parked"):     # a park survives a step boundary; only
        pos["parked"] = True            # start_step (i.e. a resolution) clears it
    st["position"] = pos
    save(job_dir, st)
    return True

def new_round(job_dir, summary=""):
    """Open round N+1 in the same job dir (spec §8): archive the finished
    step ledger into state['rounds'] and reset the nine steps to pending so
    start_step works again. Nothing else is touched — the invocation ledger,
    costs, gates and escalations accumulate across rounds, and prior
    artifacts are versioned, never deleted. Refuses while a step is still
    open, so a round can never be reset out from under itself."""
    st = load(job_dir)
    open_steps = [s["n"] for s in st["steps"] if s["status"] != "completed"]
    if open_steps:
        fail("round_in_progress",
             f"steps {open_steps} are not completed — finish or escalate the "
             f"current round before opening the next one")
    st.setdefault("rounds", []).append(
        {"round": len(st.get("rounds", [])) + 1, "closed": now(),
         "summary": summary, "steps": st["steps"]})
    st["steps"] = [{"n": s["n"], "name": s["name"], "status": "pending"}
                   for s in st["steps"]]
    st["position"] = {"step": 1, "name": st["steps"][0]["name"]}
    # everything before this index belongs to a closed round: a resumable
    # stage must not read a prior round's success as its own
    st["ledger_from"] = len(st["ledger"])
    save(job_dir, st)
    return len(st["rounds"]) + 1

# ---------------- gates, escalations, budget, batches ----------------------

def record_gate(job_dir, gate, decider, outcome, evidence, rationale):
    """Every gate outcome is auditable, including a delegated self-approval
    (spec §5.2): decider 'agent' must carry evidence paths."""
    if decider not in ("human", "agent"):
        fail("bad_decider", f"decider must be human|agent, got {decider!r}")
    if outcome not in ("approved", "rejected"):
        fail("bad_outcome", f"outcome must be approved|rejected, got {outcome!r}")
    st = load(job_dir)
    rec = {"ts": now(), "gate": gate, "decider": decider, "outcome": outcome,
           "evidence": list(evidence or []), "rationale": rationale}
    st["gates"].append(rec)
    save(job_dir, st)
    return rec

def escalate(job_dir, trigger, note_id, blocked_on, evidence, options):
    """Park the round on a human decision (spec §5.4). The object is the
    escalation: what is blocked, the evidence to judge it on, and the
    concrete options — never a free-text plea."""
    st = load(job_dir)
    rec = {"id": f"ESC{len(st['escalations']) + 1:03d}", "ts": now(),
           "trigger": trigger, "note_id": note_id, "blocked_on": blocked_on,
           "evidence": list(evidence or []), "options": list(options or []),
           "resolved": False}
    st["escalations"].append(rec)
    st["position"]["parked"] = True
    save(job_dir, st)
    return rec

def check_budget(job_dir, projected_usd):
    """Called BEFORE any paid submission (spec §5.4.2). job.yaml's ceiling
    is authority and is synced into state on every check; returns
    (ok, ceiling, actual) where ok means actual + projected fits."""
    st = load(job_dir)
    raw = job_config(job_dir).get("budget_ceiling_usd")
    ceiling = float(raw) if raw else float(st["budget"].get("ceiling_usd", 0.0))
    actual = float(st["budget"].get("actual_usd", 0.0))
    if st["budget"].get("ceiling_usd") != ceiling:
        st["budget"]["ceiling_usd"] = ceiling
        save(job_dir, st)
    return (actual + float(projected_usd) <= ceiling + 1e-9), ceiling, actual

def batch_update(job_dir, batch_id, note_id, task_id, status):
    """Per-note batch position so a killed round resumes mid-flight instead
    of resubmitting: some notes verified while others still poll is a
    representable state. task_id=None keeps the recorded one."""
    st = load(job_dir)
    b = st["batches"].setdefault(batch_id, {})
    e = b.setdefault(note_id, {"task_id": None, "status": None, "ts": None})
    if task_id is not None:
        e["task_id"] = task_id
    e["status"] = status
    e["ts"] = now()
    save(job_dir, st)
    return e

def batch_state(job_dir, batch_id):
    return load(job_dir)["batches"].get(batch_id, {})

# ---------------- CLI ------------------------------------------------------

def show(job_dir, tail):
    check_budget(job_dir, 0.0)          # sync the ceiling from job.yaml first
    st = load(job_dir)
    pos, bud = st["position"], st["budget"]
    print(f"job:      {st['job_id']}  ({st['mode']})")
    print(f"position: step {pos['step']} ({pos['name']})"
          + ("  *PARKED on escalation*" if pos.get("parked") else ""))
    print(f"budget:   ${bud.get('actual_usd', 0.0):.2f} actual of "
          f"${bud.get('ceiling_usd', 0.0):.2f} ceiling")
    for s in st["steps"]:
        mark = {"completed": "x", "in_progress": ">", "pending": " "}[s["status"]]
        print(f"  [{mark}] {s['n']} {s['name']}"
              + (f" - {s['summary']}" if s.get("summary") else ""))
    for bid, notes in st["batches"].items():
        counts = {}
        for e in notes.values():
            counts[e["status"]] = counts.get(e["status"], 0) + 1
        print(f"batch {bid}: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    for e in st["escalations"]:
        if not e["resolved"]:
            print(f"OPEN {e['id']} [{e['trigger']}] {e['note_id'] or '-'}: {e['blocked_on']}")
    print(f"ledger ({len(st['ledger'])} invocations, last {tail}):")
    for x in st["ledger"][-tail:]:
        print(f"  {x['ts']} exit={x['exit']} {x['duration_s']}s "
              f"${x['cost_usd']:.4f}  {' '.join(str(a) for a in x['argv'])}")

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_dir", help="jobs/<job-id> directory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a tool through the ledger")
    r.add_argument("--cost-usd", type=float, default=0.0,
                   help="spend this invocation adds to budget.actual_usd")
    r.add_argument("--capture", action="store_true",
                   help="capture child output instead of streaming it")
    r.add_argument("argv", nargs=argparse.REMAINDER,
                   help="-- <tool.py> [args...]")
    s = sub.add_parser("show", help="print position, budget and ledger tail")
    s.add_argument("--tail", type=int, default=10)
    args = ap.parse_args()

    job_dir = args.job_dir.rstrip("/\\")
    if args.cmd == "show":
        return show(job_dir, args.tail)
    argv = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
    if not argv:
        fail("empty_argv", "nothing to run — usage: <job_dir> run [--cost-usd X] "
                           "-- <tool.py> [args...]")
    cp = run_tool(job_dir, argv, cost_usd=args.cost_usd, capture=args.capture)
    if args.capture:
        sys.stdout.write(cp.stdout or "")
        sys.stderr.write(cp.stderr or "")
    sys.exit(cp.returncode)

if __name__ == "__main__":
    main()
