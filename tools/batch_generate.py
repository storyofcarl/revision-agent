#!/usr/bin/env python3
"""Standalone-mode batch generation for revision jobs: submit all
stills-approved notes as Seedance tasks (rolling window), poll to
completion, download candidates into lineage. Resume-safe: rerun after
interruption and it continues from notes.json status.

Usage:
  python batch_generate.py <job_dir> <notes.json> [--dry-run] [--max-concurrent N]
  python batch_generate.py --probe-quota [--confirm] [--max-tasks N]

Payload specs: the orchestrator writes revision/payloads/<note_id>.json per
references/prompt-patterns.md before running this:
  {"model_key":"seedance_pro|seedance_fast|seedance_mini",
   "prompt":"...", "ref_video":"local path or URL or null",
   "first_frame":"path|null", "last_frame":"path|null",
   "reference_images":["path"...], "ref_audio":"path|null",
   "ratio":"16:9", "resolution":"720p", "duration":8.0,
   "generate_audio":true}
Local ref_video paths are auto-hosted via Supabase storage (content-hash
cache in revision/uploads.json, 7-day retention).
occ-native jobs use `occ generate` instead of this script.

--probe-quota empirically measures the account's real concurrent-task
ceiling by ramping Seedance Mini submissions until the API refuses
(429/queue-full), then reports the highest sustained in-flight count to
stdout and quota-probe.json. THIS IS A PAID OPERATION: it prints an
estimated cost and refuses to submit anything without an explicit
--confirm. Probe once, then set max_concurrent = ceiling minus headroom
for other projects sharing the Ark account.
"""
import argparse, json, os, sys, time
import ark_client as ark

MAX_CONCURRENT_DEFAULT = 3   # documented account ceiling at <=1080p (3 tasks / 180 RPM)
CHECK_INTERVAL_S = 8         # rolling-window status sweep; tasks run minutes

# --probe-quota economics: cheapest viable config is Seedance Mini 480p, 4s,
# no reference video. tokens/s = (864*480*24)/1024 ≈ 9,720; at $3.5/M that is
# ~$0.034/s → ~$0.14 per 4s probe task.
PROBE_TASK_EST_USD = 0.14
PROBE_PROMPT = ("A plain gray sphere rotating slowly on a neutral gray "
                "background, flat studio lighting. No music. No text/logos.")

def save(doc, path):
    json.dump(doc, open(path, "w"), indent=2)

def run_batch(job_dir, notes_path, dry, max_concurrent):
    doc = json.load(open(notes_path))
    queue = [n for n in doc["notes"] if n["status"] == "stills_approved"]
    if not queue:
        sys.exit("Nothing queued: no notes at status stills_approved. "
                 "(Gate 3 not passed, or batch already ran — check statuses.)")
    cache = f"{job_dir}/revision/uploads.json"
    plans = []
    for n in queue:
        p_path = f"{job_dir}/revision/payloads/{n['note_id']}.json"
        if not os.path.exists(p_path):
            sys.exit(f"{n['note_id']}: missing payload spec {p_path} — assemble "
                     f"it per references/prompt-patterns.md before batching.")
        plans.append((n, json.load(open(p_path))))
    print(f"{len(plans)} notes queued at max {max_concurrent} concurrent "
          f"({doc['working_res']}).")
    if dry:
        for n, p in plans:
            rv = p.get("ref_video")
            print(f"  {n['note_id']}: m{n['method']} {p['model_key']} "
                  f"{p['resolution']} {p['duration']}s "
                  f"refs[video={'local->upload' if rv and not ark.is_remote(rv) else bool(rv)}, "
                  f"frames={bool(p.get('first_frame'))}/{bool(p.get('last_frame'))}, "
                  f"imgs={len(p.get('reference_images', []))}] :: "
                  f"{p['prompt'][:70]}...")
        print("Dry run only — nothing submitted, nothing spent.")
        return

    active = {}   # note_id -> (note, task_id)
    pending = list(plans)
    # re-attach: an interrupted run's in-flight tasks resume from
    # lineage.active_task instead of resubmitting (paid work is kept)
    for n, p in list(pending):
        tid = n.get("lineage", {}).get("active_task")
        if tid:
            active[n["note_id"]] = (n, tid)
            pending.remove((n, p))
            print(f"re-attached {n['note_id']} -> task {tid}")
    while pending or active:
        while pending and len(active) < max_concurrent:
            n, p = pending.pop(0)
            try:
                rv = p.get("ref_video")
                if rv and not ark.is_remote(rv):
                    local = os.path.join(job_dir, rv)
                    if not os.path.exists(local):
                        local = rv          # repo-relative / absolute path
                    # host a working-res proxy — Ark fetches the URL itself
                    # and times out on hi-res sources
                    proxy = ark.proxy_clip(local, os.path.join(
                        job_dir, "revision", "proxies",
                        os.path.basename(str(rv))))
                    rv = ark.upload_hosted(proxy, cache)
                content = ark.build_content(p["prompt"], ref_video_url=rv,
                                            first_frame=p.get("first_frame"),
                                            last_frame=p.get("last_frame"),
                                            reference_images=p.get("reference_images", []),
                                            ref_audio=p.get("ref_audio"))
                tid = None
                for attempt in (1, 2, 3):
                    try:
                        tid = ark.submit_video(
                            ark.ENDPOINTS[p["model_key"]], content,
                            ratio=p["ratio"], resolution=p["resolution"],
                            duration=p["duration"],
                            generate_audio=p.get("generate_audio", True))
                        break
                    except Exception as e:
                        print(f"  {n['note_id']}: submit attempt {attempt} "
                              f"failed: {e}")
                        if attempt < 3:
                            time.sleep(5 * attempt)
                if tid is None:
                    raise RuntimeError("all submit attempts failed")
            except Exception as e:
                # one bad submission must never kill the batch or orphan
                # the in-flight tasks
                print(f"SUBMIT FAILED {n['note_id']}: {e}")
                n["status"] = "failed"
                save(doc, notes_path)
                continue
            n["lineage"]["active_task"] = tid
            save(doc, notes_path)        # task id on disk before we rely on it
            active[n["note_id"]] = (n, tid)
            print(f"submitted {n['note_id']} -> task {tid}")
        time.sleep(CHECK_INTERVAL_S)
        for nid in list(active):
            n, tid = active[nid]
            try:
                st, url, _ = ark.check_video(tid)
            except Exception as e:               # transient poll error: keep trying
                print(f"  {nid}: poll error ({e}); will retry")
                continue
            if st == "succeeded":
                k = len(n["lineage"]["candidates"]) + 1
                out = ark.download(url, f"{job_dir}/candidates/{nid}_try{k}.mp4")
                n["lineage"]["candidates"].append(
                    {"path": os.path.relpath(out, job_dir),
                     "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "verdict": None})
                n["status"] = "generated"
                n["lineage"].pop("active_task", None)
                save(doc, notes_path)            # after EVERY completion: resume loses nothing
                del active[nid]
                print(f"done {nid} -> {out}")
            elif st == "failed":
                n["status"] = "failed"
                n["lineage"].pop("active_task", None)
                save(doc, notes_path)
                del active[nid]
                print(f"FAILED {nid} (task {tid}) — diagnose before resubmitting; "
                      f"a uniform failure is a config/prompt problem, not bad luck.")
    done = sum(1 for n in doc["notes"] if n["status"] == "generated")
    failed = [n["note_id"] for n in doc["notes"] if n["status"] == "failed"]
    print(f"Batch complete: {done} generated"
          + (f", FAILED: {', '.join(failed)}" if failed else "")
          + ". Next: verification (video-qc --profile smoke).")

# ---------------- quota probe ---------------------------------------------
class QuotaExceeded(Exception):
    """Raised by a submit function when the API refuses on quota (429/queue-full)."""

def _live_probe_submit():
    content = ark.build_content(PROBE_PROMPT)
    try:
        return ark.submit_video(ark.ENDPOINTS["seedance_mini"], content,
                                ratio="16:9", resolution="480p", duration=4,
                                generate_audio=False)
    except Exception as e:
        msg = str(e).lower()
        if "429" in msg or "quota" in msg or "queue" in msg or "rate" in msg:
            raise QuotaExceeded(str(e))
        raise

def _live_probe_status(task_id):
    st, _, _ = ark.check_video(task_id)
    return st

def probe_quota(max_tasks, submit_fn=None, status_fn=None, sleep_fn=time.sleep,
                out_path="quota-probe.json"):
    """Ramp submissions until the API refuses; report the highest sustained
    in-flight count. submit_fn/status_fn injectable for tests (submit_fn
    raises QuotaExceeded at the ceiling)."""
    submit_fn = submit_fn or _live_probe_submit
    status_fn = status_fn or _live_probe_status
    in_flight, ceiling, refused = [], 0, False
    t0 = time.time()
    submitted = 0
    while submitted < max_tasks and not refused:
        try:
            tid = submit_fn()
            in_flight.append(tid)
            submitted += 1
            ceiling = max(ceiling, len(in_flight))
            print(f"probe: {len(in_flight)} in flight (task {tid})")
        except QuotaExceeded as e:
            refused = True
            print(f"probe: refused at {len(in_flight)} in flight ({e})")
        # drop tasks that finished while ramping so 'sustained' is honest
        still = []
        for tid in in_flight:
            try:
                st = status_fn(tid)
            except Exception:
                st = "running"
            if st in ("succeeded", "failed"):
                continue
            still.append(tid)
        in_flight = still
        if not refused:
            sleep_fn(1)
    elapsed = max(time.time() - t0, 1e-9)
    report = {"ceiling": ceiling,
              "refused_by_api": refused,
              "tasks_submitted": submitted,
              "qps_observed": round(submitted / elapsed, 3),
              "est_cost_usd": round(submitted * PROBE_TASK_EST_USD, 2),
              "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    json.dump(report, open(out_path, "w"), indent=2)
    print(json.dumps(report, indent=2))
    if not refused:
        print(f"NOTE: never refused — real ceiling is >= {ceiling}. "
              f"Re-run with a higher --max-tasks to find it.")
    print("Probe tasks were left to drain (Ark bills per generation; they "
          "complete on their own). Set max_concurrent = ceiling - headroom "
          "for other projects on this account.")
    return report

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_dir", nargs="?", help="job directory (batch mode)")
    ap.add_argument("notes", nargs="?", help="path to notes.json (batch mode)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the submission plan; nothing submitted, nothing spent")
    ap.add_argument("--max-concurrent", type=int, default=MAX_CONCURRENT_DEFAULT,
                    help=f"simultaneous Ark tasks (default {MAX_CONCURRENT_DEFAULT}; "
                         f"set from job.yaml / quota-probe.json)")
    ap.add_argument("--probe-quota", action="store_true",
                    help="PAID: measure the real concurrent-task ceiling by ramping "
                         "Seedance Mini submissions until refused. Requires --confirm.")
    ap.add_argument("--confirm", action="store_true",
                    help="required with --probe-quota: acknowledge the printed cost")
    ap.add_argument("--max-tasks", type=int, default=8,
                    help="probe: hard cap on tasks submitted (default 8)")
    args = ap.parse_args()

    ark.load_env()
    if args.probe_quota:
        est = args.max_tasks * PROBE_TASK_EST_USD
        print(f"Quota probe: up to {args.max_tasks} Seedance Mini 480p/4s tasks, "
              f"estimated cost ${est:.2f}.")
        if not args.confirm:
            sys.exit("Refusing to submit without --confirm. Nothing was spent.")
        probe_quota(args.max_tasks)
        return
    if not args.job_dir or not args.notes:
        ap.error("job_dir and notes are required unless --probe-quota")
    run_batch(args.job_dir.rstrip("/"), args.notes, args.dry_run,
              args.max_concurrent)

if __name__ == "__main__":
    main()
