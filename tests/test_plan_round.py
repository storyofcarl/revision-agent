"""Brief 02 §6 acceptance: the scripted no-spend round. Every assertion
reads state.json / notes.json / the produced artifacts — nothing is judged by
eye, and nothing here touches the network or spends a cent."""
import json, os, shutil, unittest, uuid
import _util as U
import state

FIXTURE = os.path.join(U.FIX, "plan_round")
CLIPS = ["SQ001-SC001-SH001", "SQ001-SC001-SH002",   # SH002 is the 2.5s sub-4s
         "SQ001-SC001-SH003", "SQ001-SC002-SH004"]

class PlanRoundCase(unittest.TestCase):
    """Scaffolds a fixture job: seeded notes, the round's own shots.json, and
    clips copied out of tests/fixtures/generated/."""
    config = ["budget_ceiling_usd=50.0"]

    def setUp(self):
        U.ensure_fixtures()
        self.job_id = f"test-{uuid.uuid4().hex[:8]}"
        self.job = os.path.join(U.REPO, "jobs", self.job_id)
        cp = U.run_tool("revise.py", self.job_id, "--notes",
                        os.path.join(FIXTURE, "notes.json"),
                        "--config", *self.config)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.notes_path = os.path.join(self.job, "revision", "notes.json")
        shutil.copyfile(os.path.join(FIXTURE, "shots.json"),
                        os.path.join(self.job, "shots.json"))
        shutil.copyfile(os.path.join(FIXTURE, "notes.json"), self.notes_path)
        for sid in CLIPS:
            shutil.copyfile(os.path.join(U.GEN, f"{sid}.mp4"),
                            os.path.join(self.job, "clips", f"{sid}.mp4"))

    def tearDown(self):
        shutil.rmtree(self.job, ignore_errors=True)

    def plan(self, *args, expect=0):
        cp = U.run_tool("plan_round.py", self.job, *args)
        self.assertEqual(cp.returncode, expect, cp.stdout + cp.stderr)
        return cp

    def notes(self):
        return {n["note_id"]: n for n in json.load(open(self.notes_path))["notes"]}

    def escalations(self, trigger):
        return [e for e in state.load(self.job)["escalations"]
                if e["trigger"] == trigger]

    def table(self):
        p = os.path.join(self.job, "out", "gate1_table.md")
        self.assertTrue(os.path.exists(p), "gate1_table.md was not written")
        return open(p, encoding="utf-8").read()

# ---------------- 1. scripted round ---------------------------------------

class TestScriptedRound(PlanRoundCase):
    def test_table_lists_every_note_with_method_and_cost(self):
        self.plan()
        table, notes = self.table(), self.notes()
        self.assertEqual(len(notes), 6)
        for nid, n in notes.items():
            est = n["est_cost"]["expected_usd"]          # written by the tool
            row = next((l for l in table.splitlines()
                        if l.startswith(f"| {nid} |")), None)
            self.assertIsNotNone(row, f"{nid} has no row in the Gate 1 table")
            self.assertIn(f"| {n['method']} ", row, f"{nid} row lacks its method")
            self.assertIn(f"${est:.2f}", row, f"{nid} row lacks its cost")
        self.assertIn("**Project total: $", table)
        self.assertIn("PLAN ONLY", table)

    def test_routing_and_costs_are_tool_computed(self):
        self.plan()
        notes, plan = self.notes(), state.load(self.job)["plan"]
        # resolve_timecodes filled resolved_shots (the fixture ships them empty)
        self.assertEqual(notes["N-001"]["resolved_shots"], ["SQ001-SC001-SH001"])
        self.assertEqual(notes["N-006"]["resolved_shots"],
                         ["SQ001-SC001-SH003", "SQ001-SC002-SH004"])
        self.assertEqual(notes["N-002"]["resolved_shots"], [])   # asset-level
        self.assertEqual(plan["methods"],
                         {"1": ["N-003"], "2": ["N-002", "N-005", "N-006"],
                          "3": ["N-001"], "4": ["N-004"]})
        self.assertAlmostEqual(
            plan["est_total_usd"],
            sum(n["est_cost"]["expected_usd"] for n in notes.values()), places=2)
        self.assertTrue(plan["within_ceiling"])
        self.assertEqual(plan["budget_ceiling_usd"], 50.0)
        by_id = {r["note_id"]: r for r in plan["batch_plan"]}
        self.assertEqual(by_id["N-003"]["ref_video"], "white pass (revision/white/)")
        self.assertEqual(by_id["N-001"]["ref_video"], "original clip")
        self.assertEqual(by_id["N-006"]["duration_s"], 8.0)      # two shots
        self.assertEqual(by_id["N-002"]["duration_s"], 4.0)      # 4s floor
        self.assertFalse(plan["batch_dry_run"]["ran"])           # nothing queued

    def test_sub4s_shot_is_bundled_and_recorded(self):
        self.plan()
        bundles = state.load(self.job)["plan"]["bundles"]
        self.assertEqual(len(bundles), 1)
        b = bundles[0]
        self.assertIn("SQ001-SC001-SH002", b["shots"])           # the 2.5s shot
        self.assertGreater(len(b["shots"]), 1, "sub-4s shot was not bundled")
        self.assertGreaterEqual(b["duration_s"], 4.0)
        self.assertNotIn("SQ001-SC002-SH004", b["shots"])        # never cross-scene
        # lineage carries it so verification scopes the whole bundle as
        # changed — on the method-1 note that owns the pass, and only there
        notes = self.notes()
        self.assertEqual(notes["N-003"]["lineage"]["bundle"], b["shots"])
        self.assertNotIn("bundle", notes["N-001"]["lineage"])   # m3, same shot
        self.assertIn("BUNDLED", self.table())

    def test_ambiguity_escalates_with_the_proposed_readings(self):
        self.plan()
        escs = self.escalations("ambiguity")
        self.assertEqual(len(escs), 1)
        e = escs[0]
        self.assertEqual(e["note_id"], "N-006")
        self.assertGreaterEqual(len(e["options"]), 2)
        self.assertEqual(e["options"],
                         self.notes()["N-006"]["proposed_interpretations"])
        self.assertIn("out/gate1_table.md", e["evidence"])
        self.assertFalse(e["resolved"])
        self.assertIsNone(self.notes()["N-006"]["interpretation"])
        self.assertIn("AMBIGUOUS", self.table())

    def test_step_one_completes_and_position_parks_at_lock(self):
        self.plan()
        st = state.load(self.job)
        self.assertEqual(st["steps"][0]["status"], "completed")
        self.assertIn("plan-only", st["steps"][0]["summary"])
        self.assertEqual(st["position"]["step"], 2)
        self.assertEqual(st["position"]["name"], "LOCK")
        self.assertTrue(st["position"]["parked"])
        self.assertEqual([s["status"] for s in st["steps"][1:]], ["pending"] * 8)

    def test_plan_run_spends_nothing_and_ledgers_everything(self):
        self.plan()
        st = state.load(self.job)
        self.assertEqual(st["budget"]["actual_usd"], 0.0)
        self.assertTrue(all(e["cost_usd"] == 0.0 for e in st["ledger"]))
        self.assertEqual([e["argv"][0] for e in st["ledger"]],
                         ["resolve_timecodes.py", "estimate_costs.py",
                          "validate_notes.py", "white_render.py"])
        self.assertTrue(all(e["exit"] == 0 for e in st["ledger"]))
        self.assertIn("--dry-run", st["ledger"][-1]["argv"])
        logged = [json.loads(l) for l in open(
            os.path.join(self.job, "logs", "tools.jsonl")).read().splitlines()]
        self.assertEqual([e["tool"] for e in logged][:4],
                         ["resolve_timecodes.py", "estimate_costs.py",
                          "validate_notes.py", "white_render.py"])

    def test_invalid_notes_is_a_clean_nonzero_error(self):
        doc = json.load(open(self.notes_path))
        doc["notes"][0]["acceptance_criteria"] = []      # no forced-choice check
        json.dump(doc, open(self.notes_path, "w"), indent=2)
        cp = self.plan(expect=1)
        self.assertEqual(U.stderr_error(cp)["error"], "invalid_notes")

    def test_missing_shots_manifest_is_a_clean_nonzero_error(self):
        os.remove(os.path.join(self.job, "shots.json"))
        cp = self.plan(expect=1)
        self.assertEqual(U.stderr_error(cp)["error"], "no_shots_manifest")

# ---------------- 2. budget escalation ------------------------------------

class TestBudgetEscalation(PlanRoundCase):
    config = ["budget_ceiling_usd=1.0"]

    def test_projected_breach_escalates_and_parks(self):
        self.plan()
        st = state.load(self.job)
        plan = st["plan"]
        self.assertFalse(plan["within_ceiling"])
        self.assertGreater(plan["est_total_usd"], 1.0)
        escs = self.escalations("budget")
        self.assertEqual(len(escs), 1)
        e = escs[0]
        self.assertIsNone(e["note_id"])                  # round-level
        self.assertIn("1.00", e["blocked_on"])
        self.assertGreaterEqual(len(e["options"]), 3)
        self.assertIn("job.yaml", e["evidence"])
        self.assertTrue(st["position"]["parked"])
        self.assertEqual(st["position"]["name"], "LOCK")
        self.assertEqual(st["budget"], {"ceiling_usd": 1.0, "actual_usd": 0.0})
        self.assertEqual(sorted(plan["escalations"]), ["ESC001", "ESC002"])

# ---------------- 3. delegated-gate audit ---------------------------------

class TestDelegatedStillsGate(PlanRoundCase):
    config = ["budget_ceiling_usd=50.0", "gates.stills=agent"]

    def test_plan_previews_the_delegation(self):
        self.plan()
        d = state.load(self.job)["plan"]["delegated_gates"]["stills"]
        self.assertEqual((d["decider"], d["preview_only"]), ("agent", True))
        self.assertIn("acceptance_criteria", d["basis"])
        previewed = {n["note_id"]: n for n in d["notes"]}
        # methods 1 and 2 are the ones that pass through Gate 3 with stills
        self.assertEqual(sorted(previewed), ["N-002", "N-003", "N-005", "N-006"])
        self.assertEqual(previewed["N-003"]["criteria"],
                         [c["question"] for c in
                          self.notes()["N-003"]["acceptance_criteria"]])
        self.assertEqual(previewed["N-003"]["evidence_dir"],
                         "stills/edited/N-003")

    def test_gate3_self_approval_reaches_the_round_report_with_evidence(self):
        self.plan()
        evidence = ["stills/edited/N-003/SQ001-SC001-SH002_0000_v2.png",
                    "stills/extracted/N-003/SQ001-SC001-SH002_0000.png"]
        state.record_gate(self.job, "stills:N-003", "agent", "approved",
                          evidence, "N-003-c1 met: sign reads OPEN in both stills")
        cp = U.run_tool("build_round_report.py", self.job)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        report = os.path.join(self.job, "out", "round_report.json")
        r = json.load(open(report))
        self.assertEqual(r["gate_config"]["stills"], "agent")
        self.assertEqual(len(r["delegated_gate_approvals"]), 1)
        d = r["delegated_gate_approvals"][0]
        self.assertEqual((d["decider"], d["outcome"]), ("agent", "approved"))
        self.assertEqual(d["evidence"], evidence)
        by_id = {n["note_id"]: n for n in r["notes"]}
        self.assertEqual([g["gate"] for g in by_id["N-003"]["gates"]],
                         ["stills:N-003"])
        cp = U.run_tool("build_round_report.py", "--check", report)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)

# ---------------- 4. resumability -----------------------------------------

class TestResumability(PlanRoundCase):
    def test_rerun_repeats_no_step_and_no_invocation(self):
        self.plan()
        before = state.load(self.job)
        cp = self.plan()
        after = state.load(self.job)
        self.assertIn("already completed", cp.stdout)
        self.assertEqual(len(after["ledger"]), len(before["ledger"]))
        self.assertEqual(after["ledger"], before["ledger"])
        self.assertEqual(after["escalations"], before["escalations"])
        self.assertEqual(after["steps"][0], before["steps"][0])   # one completion
        self.assertEqual(after["plan"]["ts"], before["plan"]["ts"])
        self.assertIn("# Gate 1", cp.stdout)                      # table replayed

    def test_interrupted_after_resolve_continues_without_repeating_it(self):
        # kill-mid-step: step 1 open, resolve_timecodes ledgered and its
        # output durable in notes.json, nothing after it run
        self.assertTrue(state.start_step(self.job, 1))
        cp = state.run_tool(self.job, ["resolve_timecodes.py", self.job,
                                       self.notes_path])
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        killed = state.load(self.job)
        self.assertEqual(len(killed["ledger"]), 1)
        self.assertEqual(killed["steps"][0]["status"], "in_progress")
        self.assertIsNone(self.notes()["N-001"].get("est_cost"))

        cp = self.plan()
        st = state.load(self.job)
        argv0 = [e["argv"][0] for e in st["ledger"]]
        self.assertEqual(argv0.count("resolve_timecodes.py"), 1,
                         "resolve_timecodes was re-run on resume")
        self.assertIn("skipping", cp.stdout)
        for tool in ("estimate_costs.py", "validate_notes.py", "white_render.py"):
            self.assertEqual(argv0.count(tool), 1, f"{tool} did not run on resume")
        self.assertGreater(self.notes()["N-001"]["est_cost"]["expected_usd"], 0)
        self.assertEqual(st["steps"][0]["status"], "completed")
        self.assertTrue(os.path.exists(
            os.path.join(self.job, "out", "gate1_table.md")))

# ---------------- 5. round_report in both terminal states -----------------

class TestRoundReportTerminalStates(PlanRoundCase):
    def report(self):
        path = os.path.join(self.job, "out", "round_report.json")
        cp = U.run_tool("build_round_report.py", self.job)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        cp = U.run_tool("build_round_report.py", "--check", path)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        return json.load(open(path))

    def test_escalated_state_validates(self):
        self.plan()
        r = self.report()
        self.assertEqual(r["terminal_state"], "escalated")
        self.assertEqual([e["trigger"] for e in r["escalations"]], ["ambiguity"])
        self.assertTrue(r["escalations"][0]["parked"])
        self.assertEqual(r["totals"]["notes"], 6)
        self.assertEqual(r["totals"]["unresolved"], 6)   # a plan addresses none
        self.assertAlmostEqual(r["totals"]["actual_usd"], 0.0, places=2)
        self.assertAlmostEqual(r["totals"]["est_usd"],
                               state.load(self.job)["plan"]["est_total_usd"],
                               places=2)

    def test_resubmitted_state_validates_after_hand_advance(self):
        self.plan()
        doc = json.load(open(self.notes_path))
        for n in doc["notes"]:                       # hand-advance the round
            n["status"] = "verified"
            n["lineage"]["candidates"] = [
                {"path": f"verified/{n['note_id']}_v1.mp4",
                 "ts": "2026-08-12T13:00:00Z", "verdict": "PASS"}]
        json.dump(doc, open(self.notes_path, "w"), indent=2)
        st = state.load(self.job)
        for e in st["escalations"]:                  # the ambiguity was answered
            e["resolved"] = True
        st["position"] = {"step": 9, "name": "RESUBMIT"}
        state.save(self.job, st)
        r = self.report()
        self.assertEqual(r["terminal_state"], "resubmitted")
        self.assertEqual(r["totals"]["unresolved"], 0)
        self.assertEqual(r["totals"]["parked_escalations"], 0)
        self.assertEqual(len(r["escalations"]), 1)   # recorded, not hidden

if __name__ == "__main__":
    unittest.main()
