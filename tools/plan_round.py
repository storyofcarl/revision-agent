#!/usr/bin/env python3
"""Plan a revision round at zero spend — the surface behind
`revise <job-id> --notes … --plan-only` (brief 02 §5, spec §5).

Usage:
  python plan_round.py <job_dir> [--notes <path>] [--max-concurrent N]

Consumes a notes.json whose judgment fields the operator has already
completed (interpretation, scope, method, acceptance_criteria) and runs the
whole pre-Gate-1 chain through state.run_tool, so every invocation lands in
the ledger and in logs/tools.jsonl:

  resolve_timecodes.py        timecodes -> resolved_shots (writes notes.json)
  estimate_costs.py           per-note + project expected cost (writes notes.json)
  validate_notes.py           the plan validator; nothing proceeds past a failure
  white_render.py --dry-run   method-1 bundle plan, incl. the sub-4s rule (§5.3)
  batch_generate.py --dry-run when payload specs exist; summarized here otherwise

Then it:
  - renders the Gate 1 notes table (note, interpretation, shots, scope,
    method, expected cost, project total) to <job_dir>/out/gate1_table.md
    and stdout;
  - escalates AMBIGUITY — a note carrying "proposed_interpretations" (2+)
    with a null interpretation is never guessed against (spec §5.4.1); the
    escalation's options are those interpretations verbatim;
  - escalates BUDGET — expected total over job.yaml's budget_ceiling_usd
    (spec §5.4.2). Nothing generates past the ceiling;
  - completes step 1 (INTAKE) and leaves the position at step 2 (LOCK);
  - writes state['plan'] = {est_total_usd, methods, bundles, batch_plan, …},
    including a delegated-stills preview when job.yaml sets
    gates.stills: agent — the self-approval itself happens at Gate 3 in a
    live round, never here.

Nothing is submitted, uploaded or spent, and Gate 1 is NOT passed: a
plan-only round ends at the table.

Idempotency: re-running once step 1 is completed is a warning no-op that
re-prints the recorded plan. An interrupted run resumes mid-chain —
resolve_timecodes/estimate_costs are skipped when the ledger already holds a
successful run of them (their output is durable in notes.json); the free
read-only stages always re-run.

Exit 0 even when escalations were raised — an escalated plan is a
successful plan run. Non-zero with a JSON error object on stderr for real
failures: missing shots.json, unresolvable timecodes, invalid notes,
missing clips for a method-1 note.
"""
import argparse, json, os, sys

import state
from _common import fail, log_invocation

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STEP_INTAKE = 1
MIN_GEN_S = 4.0                  # Seedance generation floor (spec §5.3)
METHOD_LABEL = {1: "white-3D + stills", 2: "stills-only",
                3: "direct video edit", 4: "full re-do"}
STILLS_METHODS = (1, 2)          # pass through Gate 3 with edited stills
RES_OK = ("720p", "1080p")       # estimate_costs.py's rate table

def rel(job_dir, path):
    return os.path.relpath(path, job_dir).replace("\\", "/")

def ran_ok(st, tool):
    """True when the ledger already holds a successful run of this tool in
    THIS round — the resume test for a stage whose output is durable. The
    slice matters: state.new_round() marks where the prior round's entries
    end, so round N+1 re-resolves and re-prices its own notes."""
    return any(e.get("exit") == 0 and str((e.get("argv") or [""])[0]).endswith(tool)
               for e in st.get("ledger", [])[st.get("ledger_from", 0):])

def stage(job_dir, argv, code, message, skip_if_done=False, quiet=False):
    """Run one chain stage through the ledger; a nonzero exit is a real
    failure of the plan, reported as a JSON error with the tool's own
    diagnosis attached (it is always more specific than ours)."""
    tool = argv[0]
    if skip_if_done and ran_ok(state.load(job_dir), tool):
        print(f"{tool}: already recorded in this round's ledger - skipping "
              f"(its output is in notes.json).")
        return None
    cp = state.run_tool(job_dir, argv)
    out = ((cp.stdout or "") + (cp.stderr or "")).strip()
    if cp.returncode:
        fail(code, message, tool=tool, exit=cp.returncode,
             detail=out.splitlines()[-20:])
    if out and not quiet:
        print(out)
    return cp

# ---------------- plan assembly -------------------------------------------

def shot_seconds(note, dur):
    return max(sum(dur.get(s, 0.0) for s in note.get("resolved_shots") or []),
               MIN_GEN_S)

def batch_row(job_dir, n, dur, res):
    """The submission each note WOULD make — derived from method, working
    res and the duration ladder. batch_generate.py owns the real payload;
    this is the plan summary the operator reads at Gate 1."""
    m = n["method"]
    spec = f"revision/payloads/{n['note_id']}.json"
    return {"note_id": n["note_id"], "method": m,
            "method_label": METHOD_LABEL.get(m, "?"),
            "model_key": "seedance_pro", "resolution": res,
            "duration_s": round(shot_seconds(n, dur), 2),
            "shots": n.get("resolved_shots") or [],
            "ref_video": ("white pass (revision/white/)" if m == 1 else
                          "original clip" if m == 3 else None),
            "stills": ("approved stills, Gate 3" if m in STILLS_METHODS else None),
            "reference_images": n.get("assets_touched") or [],
            "payload_spec": spec,
            "payload_present": os.path.exists(os.path.join(job_dir, spec))}

def white_bundles(job_dir, notes, manifest):
    """Method-1 notes only: white_render.py --dry-run plans the passes and
    applies the sub-4s bundling rule. Its plan is the authority — we never
    re-implement bundling here."""
    shots, seen = [], set()
    for n in notes:
        if n["method"] != 1:
            continue
        for s in n.get("resolved_shots") or []:
            if s not in seen:
                seen.add(s)
                shots.append(s)
    if not shots:
        return []
    clips = [os.path.join(job_dir, "clips", s + ".mp4") for s in shots]
    missing = [rel(job_dir, c) for c in clips if not os.path.exists(c)]
    if missing:
        fail("no_such_clip",
             f"method-1 notes need their source clips in clips/ — missing "
             f"{', '.join(missing)}", missing=missing)
    cp = stage(job_dir, ["white_render.py", "--clips", *clips,
                         "--manifest", manifest, "--job-dir", job_dir,
                         "--dry-run"],
               "white_plan_failed",
               "white_render.py --dry-run could not plan the method-1 passes "
               "(a sub-4s shot with no bundle partner is an error, not a guess)",
               quiet=True)
    return json.loads(cp.stdout)["plan"]

def render_table(cfg, doc, ceiling, actual, bundles, rows, ambiguous):
    """The Gate 1 package, ASCII only (it is read on whatever codepage the
    host defaults to) and rendered from notes.json — never hand-written."""
    notes = doc["notes"]
    total = float((doc.get("totals") or {}).get("expected_usd") or 0.0)
    L = [f"# Gate 1 - notes table (round {doc.get('round')}, "
         f"job {cfg.get('job_id', '?')})", "",
         f"Mode {cfg.get('mode')} | working res {doc.get('working_res')} | "
         f"gates.lock: {cfg['gates'].get('lock', 'human')} | PLAN ONLY - "
         f"nothing submitted, nothing uploaded, nothing spent.", "",
         "| Note | Interpretation | Shots | Scope | Method | Expected $ |",
         "|---|---|---|---|---|---|"]
    for n in notes:
        interp = (n.get("interpretation") or "").replace("|", "/").strip()
        if not interp:
            interp = (f"AMBIGUOUS - {len(n.get('proposed_interpretations') or [])} "
                      f"readings proposed, escalated for a decision")
        est = float((n.get("est_cost") or {}).get("expected_usd") or 0.0)
        L.append(f"| {n['note_id']} | {interp} | "
                 f"{', '.join(n.get('resolved_shots') or []) or '(asset-level)'} | "
                 f"{n.get('scope')} | {n['method']} {METHOD_LABEL.get(n['method'], '?')} | "
                 f"${est:.2f} |")
    L += ["", f"**Project total: ${total:.2f} expected** across {len(notes)} notes, "
              f"against a ${ceiling:.2f} ceiling with ${actual:.2f} spent so far. "
              f"Per-note figures are estimate_costs.py output - never "
              f"hand-computed (routing-and-costs.md).", ""]

    L += ["## White-pass bundles (method 1, sub-4s rule spec 5.3)", ""]
    if not bundles:
        L.append("(no method-1 notes in this round)")
    for b in bundles:
        tag = " BUNDLED" if len(b["shots"]) > 1 else ""
        L.append(f"- `{b['render']}`{tag}: {' + '.join(b['shots'])} -> "
                 f"{b['duration_s']}s, est ${b['est_usd']:.2f} (Seedance Mini 480p)")
    L += ["", "## Generation plan (nothing submitted)", "",
          "| Note | Model | Res | Duration | Video ref | Stills | Payload spec |",
          "|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['note_id']} | {r['model_key']} | {r['resolution']} | "
                 f"{r['duration_s']}s | {r['ref_video'] or '-'} | "
                 f"{r['stills'] or '-'} | `{r['payload_spec']}`"
                 f"{'' if r['payload_present'] else ' (not written yet)'} |")
    if ambiguous:
        L += ["", "## Ambiguity - escalated, never guessed against (spec 5.4.1)", ""]
        for n in ambiguous:
            L.append(f"- **{n['note_id']}** \"{n['raw_text']}\"")
            for i, o in enumerate(n["proposed_interpretations"], 1):
                L.append(f"  {i}. {o}")
    L += ["", f"Gate 1 decision (job.yaml gates.lock: "
              f"{cfg['gates'].get('lock', 'human')}): approve the table, amend a "
              f"row, or re-scope. A plan-only round stops here - do not pass "
              f"Gate 1 from a plan run.", ""]
    return "\n".join(L)

def replay(job_dir):
    """Step 1 already completed: show what is on file, run nothing."""
    st = state.load(job_dir)
    table = os.path.join(job_dir, "out", "gate1_table.md")
    if os.path.exists(table):
        sys.stdout.write(open(table, encoding="utf-8").read())
    p = st.get("plan")
    if p:
        print(f"Plan on file ({p['ts']}): ${p['est_total_usd']:.2f} expected "
              f"across {p['notes']} notes, {len(p['bundles'])} white-pass "
              f"bundle(s), escalations {p.get('escalations') or []}.")
    print("Step 1 (INTAKE) already completed - nothing re-run, nothing spent. "
          f"Resume at step {st['position']['step']} ({st['position']['name']}).")

# ---------------- main -----------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_dir", help="jobs/<job-id> directory")
    ap.add_argument("--notes", help="notes.json to plan from "
                                    "(default <job_dir>/revision/notes.json)")
    ap.add_argument("--max-concurrent", type=int,
                    help="simultaneous Ark tasks the plan assumes "
                         "(default job.yaml max_concurrent)")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        # the table quotes client prose; a cp1252 console must not kill a plan
        sys.stdout.reconfigure(errors="replace")

    job_dir = os.path.abspath(args.job_dir.rstrip("/\\"))
    if not os.path.isdir(job_dir):
        fail("no_such_job", f"{job_dir} is not a directory — scaffold the round "
                            f"with revise.py first")
    notes_path = os.path.abspath(args.notes or
                                 os.path.join(job_dir, "revision", "notes.json"))
    if not os.path.exists(notes_path):
        fail("no_notes_json", f"{notes_path} does not exist — normalize the "
                              f"intake (intake_normalize.py) and complete the "
                              f"judgment fields before planning")
    manifest = os.path.join(job_dir, "shots.json")
    if not os.path.exists(manifest):
        fail("no_shots_manifest",
             f"{manifest} does not exist — a plan needs the lineup's duration "
             f"ladder (shot_id + duration_s in lineup order)")

    if not state.start_step(job_dir, STEP_INTAKE):
        return replay(job_dir)

    cfg = state.job_config(job_dir)
    doc = json.load(open(notes_path))
    # estimate_costs.py reads <doc.job_dir>/shots.json: point it at this
    # round's own manifest, repo-relative so notes.json stays portable
    # (ledgered tools run with the repo root as cwd).
    r = os.path.relpath(job_dir, REPO).replace("\\", "/")
    doc["job_dir"] = r if not r.startswith("..") else job_dir.replace("\\", "/")
    res = doc.get("working_res") or cfg.get("working_res", "720p")
    if res not in RES_OK:
        fail("bad_working_res", f"working_res {res!r} is not one of "
                                f"{'/'.join(RES_OK)} — revision rounds never "
                                f"run 4K (routing-and-costs.md)")
    doc["working_res"] = res
    json.dump(doc, open(notes_path, "w"), indent=2)

    stage(job_dir, ["resolve_timecodes.py", job_dir, notes_path],
          "resolve_failed",
          "resolve_timecodes.py could not place every note against the lineup — "
          "confirm the fps base or the cut version, do not guess",
          skip_if_done=True)
    stage(job_dir, ["estimate_costs.py", notes_path], "estimate_failed",
          "estimate_costs.py could not price the round", skip_if_done=True)
    stage(job_dir, ["validate_notes.py", notes_path], "invalid_notes",
          "notes.json does not pass the plan validator — Gate 1 cannot proceed")

    doc = json.load(open(notes_path))
    notes = doc["notes"]
    dur = {s["shot_id"]: float(s["duration_s"]) for s in json.load(open(manifest))}
    bundles = white_bundles(job_dir, notes, manifest)
    for n in notes:                       # bundle lineage on the method-1 note
        for b in bundles:                 # that owns the pass, so verification
            if n["method"] == 1 and len(b["shots"]) > 1 and \
                    set(n.get("resolved_shots") or []) & set(b["shots"]):
                n.setdefault("lineage", {})["bundle"] = b["shots"]
    json.dump(doc, open(notes_path, "w"), indent=2)

    rows = [batch_row(job_dir, n, dur, res) for n in notes]
    queued = [n for n in notes if n.get("status") == "stills_approved"]
    if queued and all(r["payload_present"] for r in rows
                      if r["note_id"] in {n["note_id"] for n in queued}):
        mc = args.max_concurrent or int(cfg.get("max_concurrent", 3))
        cp = stage(job_dir, ["batch_generate.py", job_dir, notes_path,
                             "--dry-run", "--max-concurrent", str(mc)],
                   "batch_plan_failed",
                   "batch_generate.py --dry-run rejected the payload specs")
        batch_dry = {"ran": True, "exit": 0,
                     "stdout": (cp.stdout or "").strip().splitlines()}
    else:
        batch_dry = {"ran": False, "why": "no notes at status stills_approved "
                     "with a payload spec - a plan-only round stops before "
                     "Gate 3, so the plan above is the summary"}

    est_total = float((doc.get("totals") or {}).get("expected_usd") or 0.0)
    ok, ceiling, actual = state.check_budget(job_dir, est_total)
    ambiguous = [n for n in notes
                 if not n.get("interpretation")
                 and len(n.get("proposed_interpretations") or []) >= 2]

    table = render_table(cfg, doc, ceiling, actual, bundles, rows, ambiguous)
    out_path = os.path.join(job_dir, "out", "gate1_table.md")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "w", encoding="utf-8").write(table)
    sys.stdout.write(table)

    # step 1 closes BEFORE escalating: complete_step advances the position,
    # escalate then parks it at LOCK where the human decision lives
    state.complete_step(job_dir, STEP_INTAKE,
                        f"plan-only: {len(notes)} notes, ${est_total:.2f} "
                        f"expected, {len(bundles)} white-pass bundle(s), "
                        f"{len(ambiguous) + (0 if ok else 1)} escalation(s)")

    escalations = []
    for n in ambiguous:
        e = state.escalate(
            job_dir, "ambiguity", n["note_id"],
            f"{n['note_id']} \"{n['raw_text']}\" has "
            f"{len(n['proposed_interpretations'])} plausible readings - pick "
            f"one at Gate 1; a note is never guessed against",
            [rel(job_dir, notes_path), "out/gate1_table.md"],
            list(n["proposed_interpretations"]))
        escalations.append(e["id"])
    if not ok:
        dear = sorted(notes, key=lambda n: -(n.get("est_cost") or {})
                      .get("expected_usd", 0.0))[:2]
        e = state.escalate(
            job_dir, "budget", None,
            f"projected ${est_total:.2f} + ${actual:.2f} already spent exceeds "
            f"the ${ceiling:.2f} ceiling - nothing generates past it (spec 5.4.2)",
            [rel(job_dir, notes_path), "out/gate1_table.md", "job.yaml"],
            [f"raise budget_ceiling_usd in job.yaml to at least "
             f"${est_total + actual:.2f} and re-plan",
             f"defer the most expensive notes ("
             + ", ".join(f"{n['note_id']} ${(n.get('est_cost') or {}).get('expected_usd', 0):.2f}"
                         for n in dear) + ") to a later round",
             "re-scope: route notes off methods 3/4 to method 2 where motion "
             "preservation is not required (routing-and-costs.md)"])
        escalations.append(e["id"])

    plan = {"ts": state.now(), "notes": len(notes), "est_total_usd": est_total,
            "budget_ceiling_usd": ceiling, "actual_usd": actual,
            "within_ceiling": ok,
            "methods": {str(m): [n["note_id"] for n in notes if n["method"] == m]
                        for m in sorted(METHOD_LABEL)
                        if any(n["method"] == m for n in notes)},
            "bundles": bundles, "batch_plan": rows, "batch_dry_run": batch_dry,
            "gate1_table": "out/gate1_table.md",
            "ambiguous_notes": [n["note_id"] for n in ambiguous],
            "escalations": escalations}
    if cfg["gates"].get("stills") == "agent":
        plan["delegated_gates"] = {"stills": {
            "gate": "stills", "decider": "agent", "preview_only": True,
            "when": "Gate 3 (step 4 STILLS) of a live round - never in a plan run",
            "basis": "the note's compiled acceptance_criteria",
            "recorded_as": "state.record_gate(gate='stills:<note_id>', "
                           "decider='agent', outcome=..., evidence=[edited + "
                           "source still paths]) - a delegated gate is "
                           "auditable, never silent (spec 5.2)",
            "notes": [{"note_id": n["note_id"],
                       "criteria": [c.get("question") for c in n["acceptance_criteria"]],
                       "evidence_dir": f"stills/edited/{n['note_id']}"}
                      for n in notes if n["method"] in STILLS_METHODS]}}
    st = state.load(job_dir)
    st["plan"] = plan
    state.save(job_dir, st)

    for e in state.load(job_dir)["escalations"]:
        if e["id"] not in escalations:
            continue
        print(f"\nESCALATION {e['id']} [{e['trigger']}]"
              + (f" {e['note_id']}" if e["note_id"] else "")
              + f"\n  blocked on: {e['blocked_on']}"
              + "".join(f"\n  option: {o}" for o in e["options"])
              + "".join(f"\n  evidence: {v}" for v in e["evidence"]))
    pos = state.load(job_dir)["position"]
    print(f"\nOK: plan complete at ${est_total:.2f} expected (ceiling "
          f"${ceiling:.2f}), {len(escalations)} escalation(s), $0.00 spent. "
          f"Table: {rel(job_dir, out_path)}. Position: step {pos['step']} "
          f"({pos['name']}){' PARKED' if pos.get('parked') else ''}.")
    log_invocation(job_dir, "plan_round.py",
                   extra={"est_total_usd": est_total, "notes": len(notes),
                          "escalations": escalations, "cost_usd": 0.0})

if __name__ == "__main__":
    main()
