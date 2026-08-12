#!/usr/bin/env python3
"""Render out/round_report.json — the round's machine-readable disposition
(spec §8.3) — from revision/notes.json + state.json only, plus job.yaml for
the gate dial. Never hand-written. A human-readable summary is rendered
FROM the JSON beside it, so the two can never disagree.

Usage:
  python build_round_report.py <job_dir> [--out <path>]
  python build_round_report.py --check <report.json>

Behavior:
  - Default output is <job_dir>/out/round_report.json with the summary at
    <job_dir>/out/round_report.md (--out swaps the .json extension for .md).
  - terminal_state is derived: any parked escalation (or a parked position)
    => "escalated", else "resubmitted".
  - Per-note actual cost is attributed from state.ledger — an entry with a
    non-zero cost_usd goes to every note whose note_id appears in its argv,
    split evenly; cost naming no note becomes unattributed_actual_usd.
  - --check validates a report against the documented required fields,
    enums and cross-field invariants; exit 1 with itemized errors.

The schema and its stability contract (external API, additive changes only)
live in agent/references/round-report-schema.md — update both together.

Exit 0 on success; non-zero with a JSON error object on stderr otherwise.
"""
import argparse, json, os, re, time

from _common import fail, log_invocation

REPORT_VERSION = "1.0.0"
METHOD_LABEL = {1: "white-3D + stills", 2: "stills-only",
                3: "direct video edit", 4: "full re-do"}
ADDRESSED = ("done", "verified")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
NUM = (int, float)

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def read_json(path, code):
    if not os.path.exists(path):
        fail(code, f"{path} does not exist — nothing to render from")
    try:
        return json.load(open(path))
    except ValueError as e:
        fail(code, f"{path} is not valid JSON: {e}")

def gate_config(job_dir):
    """Parse job.yaml's gates block (flat template text, no yaml dep)."""
    path = os.path.join(job_dir, "job.yaml")
    if not os.path.exists(path):
        return None, None
    text = open(path).read()
    gates = {k: v for k, v in re.findall(r"^  (lock|foundation|stills): (\w+)",
                                         text, flags=re.M)}
    m = re.search(r"^budget_ceiling_usd: ([\d.]+)", text, flags=re.M)
    return (gates or None), (float(m.group(1)) if m else None)

def attribute(gate_or_esc, ids):
    """A gate/escalation belongs to a note when it says so, or when a note
    id appears in its gate string (e.g. "stills:N-001")."""
    nid = gate_or_esc.get("note_id")
    if nid:
        return nid
    label = str(gate_or_esc.get("gate", ""))
    return next((i for i in ids if i in label), None)

def ledger_costs(ledger, ids):
    """Split each priced ledger entry across the notes its argv names; cost
    naming no note stays unattributed (the residual against the budget)."""
    per = {i: 0.0 for i in ids}
    for e in ledger:
        cost = float(e.get("cost_usd") or 0.0)
        if not cost:
            continue
        argv = " ".join(str(a) for a in e.get("argv", []))
        hit = [i for i in ids if i in argv]
        for i in hit:
            per[i] += cost / len(hit)
    return per

def note_entry(n, gates, escs, actual):
    lin = n.get("lineage") or {}
    cands = lin.get("candidates") or []
    ver = []
    for c in cands:
        refs = list(c.get("qc_refs") or [])
        if c.get("qc_ref"):
            refs.append(c["qc_ref"])
        ver.append({"candidate": c.get("path"), "verdict": c.get("verdict"),
                    "ts": c.get("ts"), "qc_refs": refs})
    entry = {
        "note_id": n["note_id"],
        "classification": n.get("scope"),
        "method": n.get("method"),
        "method_label": METHOD_LABEL.get(n.get("method"), "unknown"),
        "status": n.get("status"),
        "raw_text": n.get("raw_text"),
        "interpretation": n.get("interpretation"),
        "resolved_shots": n.get("resolved_shots", []),
        "assets_touched": n.get("assets_touched", []),
        "cycles_used": lin.get("revision_cycle", len(cands)),
        "gates": gates,
        "verification": ver,
        "qc_refs": lin.get("qc_refs", []),
        "escalations": [e["trigger"] for e in escs],
        "cost": {"est_usd": round(float((n.get("est_cost") or {})
                                        .get("expected_usd") or 0.0), 2),
                 "actual_usd": round(actual, 2)},
    }
    if lin.get("bundle") is not None:
        entry["bundle"] = lin["bundle"]
    if n.get("rationale") is not None:
        entry["rationale"] = n["rationale"]
    return entry

def build(job_dir):
    notes_doc = read_json(os.path.join(job_dir, "revision", "notes.json"),
                          "no_notes_json")
    state = read_json(os.path.join(job_dir, "state.json"), "no_state_json")
    gates_cfg, ceiling = gate_config(job_dir)
    notes = notes_doc.get("notes", [])
    ids = [n["note_id"] for n in notes]

    all_gates = state.get("gates", [])
    by_note_gates = {i: [] for i in ids}
    round_gates = []
    for g in all_gates:
        entry = dict(g)
        entry.setdefault("evidence", [])
        nid = attribute(g, ids)
        if nid:
            entry["note_id"] = nid
            by_note_gates[nid].append(entry)
        else:
            round_gates.append(entry)

    escalations, by_note_escs = [], {i: [] for i in ids}
    for e in state.get("escalations", []):
        entry = dict(e)
        entry.setdefault("evidence", [])
        entry.setdefault("options", [])
        entry["parked"] = not bool(e.get("resolved"))
        escalations.append(entry)
        nid = attribute(e, ids)
        if nid:
            entry["note_id"] = nid
            by_note_escs[nid].append(entry)

    ledger = state.get("ledger", [])
    per_note = ledger_costs(ledger, ids)
    budget = state.get("budget", {})
    actual_total = round(float(budget.get("actual_usd") or 0.0), 2)

    entries = [note_entry(n, by_note_gates[n["note_id"]],
                          by_note_escs[n["note_id"]], per_note[n["note_id"]])
               for n in notes]
    # totals.actual_usd is authoritative; the residual is the unattributed
    # slice, so the report's cost rows always sum to it
    attributed = round(sum(e["cost"]["actual_usd"] for e in entries), 2)
    est_total = round(sum(e["cost"]["est_usd"] for e in entries), 2)
    addressed = sum(1 for e in entries if e["status"] in ADDRESSED)
    parked = [e for e in escalations if e["parked"]]
    delegated = [g for g in all_gates if g.get("decider") == "agent"]
    terminal = ("escalated" if parked or state.get("position", {}).get("parked")
                else "resubmitted")

    report = {
        "report_version": REPORT_VERSION,
        "job_id": state.get("job_id"),
        "round": notes_doc.get("round"),
        "mode": state.get("mode"),
        "terminal_state": terminal,
        "generated_ts": now(),
        "gate_config": gates_cfg,
        "budget": {"ceiling_usd": ceiling
                   if ceiling is not None else budget.get("ceiling_usd"),
                   "estimated_usd": est_total, "actual_usd": actual_total},
        "notes": entries,
        "round_gates": round_gates,
        "delegated_gate_approvals": [dict(g, evidence=g.get("evidence", []))
                                     for g in delegated],
        "escalations": escalations,
        "totals": {"notes": len(entries), "addressed": addressed,
                   "unresolved": len(entries) - addressed,
                   "est_usd": est_total, "actual_usd": actual_total,
                   "unattributed_actual_usd": round(actual_total - attributed, 2),
                   "escalations": len(escalations),
                   "parked_escalations": len(parked),
                   "delegated_gate_approvals": len(delegated),
                   "tool_invocations": len(ledger),
                   "failed_invocations": sum(1 for e in ledger if e.get("exit"))},
    }
    if gates_cfg is None:
        report.pop("gate_config")
    return report

def render_md(r):
    """Human-readable summary — rendered FROM the report, never beside it.
    ASCII only: this file is read on whatever codepage the host defaults to."""
    t = r["totals"]
    out = [f"# Round {r['round']} - {r['job_id']} - {r['terminal_state'].upper()}", "",
           f"Generated {r['generated_ts']} | report_version {r['report_version']}"
           f" | mode {r.get('mode')}", "",
           f"{t['notes']} notes: **{t['addressed']} addressed, "
           f"{t['unresolved']} unresolved**. "
           f"Cost ${t['actual_usd']:.2f} actual vs ${t['est_usd']:.2f} estimated "
           f"(ceiling ${r['budget']['ceiling_usd']}).", "",
           "| Note | Scope | Method | Status | Cycles | Est | Actual | Verdicts |",
           "|---|---|---|---|---|---|---|---|"]
    for n in r["notes"]:
        verdicts = ", ".join(str(v["verdict"]) for v in n["verification"]) or "-"
        out.append(f"| {n['note_id']} | {n['classification']} | "
                   f"{n['method']} {n['method_label']} | {n['status']} | "
                   f"{n['cycles_used']} | ${n['cost']['est_usd']:.2f} | "
                   f"${n['cost']['actual_usd']:.2f} | {verdicts} |")
    out += ["", "## Gates", ""]
    gates = r["round_gates"] + [g for n in r["notes"] for g in n["gates"]]
    if not gates:
        out.append("(none recorded)")
    for g in gates:
        tag = " - DELEGATED SELF-APPROVAL" if g.get("decider") == "agent" else ""
        out.append(f"- **{g.get('gate')}** {g.get('outcome')} by "
                   f"{g.get('decider')}{tag}"
                   + (f" ({g['note_id']})" if g.get("note_id") else ""))
        if g.get("rationale"):
            out.append(f"  - rationale: {g['rationale']}")
        for e in g.get("evidence", []):
            out.append(f"  - evidence: `{e}`")
    out += ["", "## Escalations", ""]
    if not r["escalations"]:
        out.append("(none)")
    for e in r["escalations"]:
        out.append(f"- **{e.get('trigger')}** "
                   f"({'PARKED' if e['parked'] else 'resolved'})"
                   + (f" - {e['note_id']}" if e.get("note_id") else "")
                   + f" - blocked on: {e.get('blocked_on')}")
        for o in e.get("options", []):
            out.append(f"  - option: {o}")
        for ev in e.get("evidence", []):
            out.append(f"  - evidence: `{ev}`")
    out += ["", "## Notes in full", ""]
    for n in r["notes"]:
        out.append(f"### {n['note_id']} - {n['status']}")
        if n.get("raw_text"):
            out.append(f"> {n['raw_text']}")
        out.append(f"- {n.get('interpretation') or '(no interpretation)'}")
        out.append(f"- shots: {', '.join(n['resolved_shots']) or '(asset-level)'}")
        if n.get("rationale"):
            out.append(f"- rationale: {n['rationale']}")
        out.append("")
    return "\n".join(out) + "\n"

def check(path):
    if not os.path.exists(path):
        fail("no_such_report", f"{path} does not exist")
    try:
        r = json.load(open(path))
    except ValueError as e:
        fail("unreadable_report", f"{path} is not valid JSON: {e}")
    if not isinstance(r, dict):
        fail("unreadable_report", f"{path} is not a JSON object")
    errs = []

    def req(obj, key, kind, where):
        if not isinstance(obj, dict) or key not in obj or obj[key] is None:
            errs.append(f"{where}.{key}: missing")
            return None
        v = obj[key]
        if kind and not isinstance(v, kind):
            errs.append(f"{where}.{key}: wrong type {type(v).__name__}")
            return None
        return v

    def enum(val, allowed, where):
        if val is not None and val not in allowed:
            errs.append(f"{where}: {val!r} not in {sorted(allowed)}")

    ver = req(r, "report_version", str, "report")
    if ver and not SEMVER.match(ver):
        errs.append(f"report.report_version: {ver!r} is not semver")
    req(r, "job_id", str, "report")
    req(r, "round", int, "report")
    req(r, "generated_ts", str, "report")
    enum(req(r, "terminal_state", str, "report"),
         {"resubmitted", "escalated"}, "report.terminal_state")
    budget = req(r, "budget", dict, "report") or {}
    for k in ("estimated_usd", "actual_usd"):
        req(budget, k, NUM, "report.budget")

    notes = req(r, "notes", list, "report") or []
    est_sum = act_sum = 0.0
    addressed = 0
    for i, n in enumerate(notes):
        w = f"notes[{i}]"
        if not isinstance(n, dict):
            errs.append(f"{w}: not an object")
            continue
        req(n, "note_id", str, w)
        enum(req(n, "classification", str, w),
             {"local", "foundational"}, f"{w}.classification")
        m = req(n, "method", int, w)
        if m is not None and m not in METHOD_LABEL:
            errs.append(f"{w}.method: {m} not in 1..4")
        req(n, "method_label", str, w)
        status = req(n, "status", str, w)
        req(n, "cycles_used", int, w)
        for k in ("gates", "verification"):
            req(n, k, list, w)
        for j, v in enumerate(n.get("verification") or []):
            if not isinstance(v, dict) or "candidate" not in v:
                errs.append(f"{w}.verification[{j}].candidate: missing")
            elif "verdict" not in v:
                errs.append(f"{w}.verification[{j}].verdict: missing")
        cost = req(n, "cost", dict, w) or {}
        e = req(cost, "est_usd", NUM, f"{w}.cost")
        a = req(cost, "actual_usd", NUM, f"{w}.cost")
        est_sum += e or 0.0
        act_sum += a or 0.0
        if status in ADDRESSED:
            addressed += 1
        for j, g in enumerate(n.get("gates") or []):
            check_gate(g, f"{w}.gates[{j}]", errs, req, enum)

    for i, g in enumerate(req(r, "round_gates", list, "report") or []):
        check_gate(g, f"report.round_gates[{i}]", errs, req, enum)
    delegated = req(r, "delegated_gate_approvals", list, "report") or []
    for i, g in enumerate(delegated):
        check_gate(g, f"report.delegated_gate_approvals[{i}]", errs, req, enum)
        if isinstance(g, dict) and g.get("decider") != "agent":
            errs.append(f"report.delegated_gate_approvals[{i}].decider: "
                        f"must be 'agent'")

    escs = req(r, "escalations", list, "report") or []
    parked = 0
    for i, e in enumerate(escs):
        w = f"report.escalations[{i}]"
        if not isinstance(e, dict):
            errs.append(f"{w}: not an object")
            continue
        req(e, "trigger", str, w)
        req(e, "blocked_on", str, w)
        req(e, "evidence", list, w)
        req(e, "options", list, w)
        if not isinstance(e.get("parked"), bool):
            errs.append(f"{w}.parked: missing")
        elif e["parked"]:
            parked += 1

    t = req(r, "totals", dict, "report") or {}
    for k in ("notes", "addressed", "unresolved"):
        req(t, k, int, "report.totals")
    for k in ("est_usd", "actual_usd", "unattributed_actual_usd"):
        req(t, k, NUM, "report.totals")
    if isinstance(t.get("notes"), int) and t["notes"] != len(notes):
        errs.append(f"report.totals.notes: {t['notes']} != {len(notes)} rendered")
    if isinstance(t.get("addressed"), int) and isinstance(t.get("unresolved"), int):
        if t["addressed"] + t["unresolved"] != len(notes):
            errs.append("report.totals: addressed + unresolved != notes")
        if t["addressed"] != addressed:
            errs.append(f"report.totals.addressed: {t['addressed']} != "
                        f"{addressed} notes with an addressed status")
    if isinstance(t.get("est_usd"), NUM) and abs(t["est_usd"] - est_sum) > 0.01:
        errs.append(f"report.totals.est_usd: {t['est_usd']} != sum of note "
                    f"est {round(est_sum, 2)}")
    if isinstance(t.get("actual_usd"), NUM) and \
       isinstance(t.get("unattributed_actual_usd"), NUM):
        if abs(t["actual_usd"] - (act_sum + t["unattributed_actual_usd"])) > 0.01:
            errs.append("report.totals.actual_usd: note actuals + "
                        "unattributed_actual_usd != actual_usd")
    if r.get("terminal_state") == "escalated" and not escs:
        errs.append("report.escalations: empty but terminal_state is escalated")
    if r.get("terminal_state") == "resubmitted" and parked:
        errs.append(f"report.escalations: {parked} parked but terminal_state "
                    f"is resubmitted")

    if errs:
        fail("invalid_report", f"{len(errs)} error(s) in {path}", errors=errs)
    print(f"OK: {path} valid (report_version {r['report_version']}, "
          f"{len(notes)} notes, terminal_state {r['terminal_state']})")

def check_gate(g, where, errs, req, enum):
    if not isinstance(g, dict):
        errs.append(f"{where}: not an object")
        return
    req(g, "gate", str, where)
    enum(req(g, "decider", str, where), {"human", "agent"}, f"{where}.decider")
    enum(req(g, "outcome", str, where), {"approved", "rejected"},
         f"{where}.outcome")
    ev = req(g, "evidence", list, where)
    if g.get("decider") == "agent" and not ev:
        errs.append(f"{where}.evidence: a delegated (agent) gate must carry "
                    f"evidence paths — spec §5.2")

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_dir", nargs="?", help="jobs/<job-id> directory")
    ap.add_argument("--out", help="report path "
                                  "(default <job_dir>/out/round_report.json)")
    ap.add_argument("--check", metavar="REPORT",
                    help="validate an existing report and exit")
    args = ap.parse_args()

    if args.check:
        check(args.check)
        return
    if not args.job_dir:
        fail("no_job_dir", "give a job_dir to render, or --check <report.json>")
    job_dir = args.job_dir
    if not os.path.isdir(job_dir):
        fail("no_such_job", f"{job_dir} is not a directory")

    report = build(job_dir)
    out = args.out or os.path.join(job_dir, "out", "round_report.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    json.dump(report, open(out, "w"), indent=2)
    md = os.path.splitext(out)[0] + ".md"
    open(md, "w", encoding="utf-8").write(render_md(report))
    log_invocation(job_dir, "build_round_report.py",
                   extra={"out": out, "terminal_state": report["terminal_state"]})
    t = report["totals"]
    print(f"OK: {out} ({report['terminal_state']}; {t['notes']} notes, "
          f"{t['addressed']} addressed, {t['unresolved']} unresolved; "
          f"${t['actual_usd']:.2f} actual vs ${t['est_usd']:.2f} est). "
          f"Summary: {md}")

if __name__ == "__main__":
    main()
