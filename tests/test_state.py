import contextlib, io, json, os, shutil, sys, unittest, uuid
import _util as U
import state

FIX = U.FIX

def stderr_of(fn, *a, **kw):
    """Run fn capturing the warning it prints; returns (result, stderr)."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()

class TestState(unittest.TestCase):
    def setUp(self):
        self.job_id = f"test-{uuid.uuid4().hex[:8]}"
        self.job = os.path.join(U.REPO, "jobs", self.job_id)
        cp = U.run_tool("revise.py", self.job_id, "--notes",
                        os.path.join(FIX, "email.txt"), "--config",
                        "budget_ceiling_usd=10.0", "gates.stills=agent")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)

    def tearDown(self):
        shutil.rmtree(self.job, ignore_errors=True)

    def _ledger(self):
        return state.load(self.job)["ledger"]

    # ---- the single runner ------------------------------------------------

    def test_runner_logs_ledger_log_file_and_cost(self):
        cp = state.run_tool(self.job, ["validate_notes.py",
                                       os.path.join(FIX, "notes_valid.json")],
                            cost_usd=1.5)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("OK", cp.stdout)                      # captured by default
        cp = state.run_tool(self.job, [sys.executable, "-c", "import sys; sys.exit(3)"],
                            cost_usd=0.25)
        self.assertEqual(cp.returncode, 3)                  # nonzero never raises
        led = self._ledger()
        self.assertEqual(len(led), 2)
        self.assertEqual(set(led[0]), {"ts", "argv", "exit", "duration_s", "cost_usd"})
        self.assertEqual([e["exit"] for e in led], [0, 3])
        self.assertGreaterEqual(led[0]["duration_s"], 0.0)
        st = state.load(self.job)
        self.assertEqual(st["budget"]["actual_usd"], 1.75)  # accumulates
        lines = open(os.path.join(self.job, "logs", "tools.jsonl")).read().splitlines()
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertEqual(first["tool"], "validate_notes.py")
        self.assertEqual(first["exit"], 0)
        self.assertEqual(first["cost_usd"], 1.5)

    def test_runner_resolves_bare_tool_name_against_tools_dir(self):
        cp = state.run_tool(self.job, ["revise.py", "--help"])
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("--plan-only", cp.stdout)

    # ---- step idempotency -------------------------------------------------

    def test_start_and_complete_step_are_idempotent(self):
        self.assertTrue(state.start_step(self.job, 1))
        self.assertTrue(state.complete_step(self.job, 1, "intake normalized"))
        st = state.load(self.job)
        self.assertEqual(st["steps"][0]["status"], "completed")
        self.assertEqual(st["position"], {"step": 2, "name": "LOCK"})

        state.run_tool(self.job, [sys.executable, "-c", "pass"])
        before = json.dumps(state.load(self.job), sort_keys=True)

        ok, err = stderr_of(state.start_step, self.job, 1)
        self.assertFalse(ok)
        self.assertIn("WARNING", err)
        ok, err = stderr_of(state.complete_step, self.job, 1, "different summary")
        self.assertFalse(ok)
        self.assertIn("WARNING", err)

        st = state.load(self.job)
        self.assertEqual(st["steps"][0]["summary"], "intake normalized")
        self.assertEqual(len(st["ledger"]), 1)              # no ledger noise
        self.assertEqual(json.dumps(st, sort_keys=True), before)   # true no-op

    def test_complete_step_keeps_the_round_parked(self):
        """A park is a property of the round, not of the step it fired in:
        completing the step it fired in must not silently un-park it."""
        state.start_step(self.job, 1)
        state.escalate(self.job, "budget", None, "ceiling", ["job.yaml"], ["raise"])
        state.complete_step(self.job, 1, "intake done, parked on budget")
        pos = state.load(self.job)["position"]
        self.assertEqual((pos["step"], pos["name"]), (2, "LOCK"))
        self.assertTrue(pos["parked"])

    def test_new_round_resets_the_ledger_for_round_two(self):
        """spec §8: new client notes loop to intake as round N+1 in the same
        job dir. The nine steps reset; costs, gates and escalations do not."""
        for n in range(1, 10):
            state.start_step(self.job, n)
            state.complete_step(self.job, n, f"step {n}")
        state.run_tool(self.job, ["validate_notes.py",
                                  os.path.join(FIX, "notes_valid.json")],
                       cost_usd=2.0)
        self.assertFalse(state.start_step(self.job, 1))     # exhausted
        self.assertEqual(state.new_round(self.job, "round 1 resubmitted"), 2)
        st = state.load(self.job)
        self.assertEqual([s["status"] for s in st["steps"]], ["pending"] * 9)
        self.assertEqual(st["position"], {"step": 1, "name": "INTAKE"})
        self.assertEqual(len(st["rounds"]), 1)
        self.assertEqual([s["n"] for s in st["rounds"][0]["steps"]], list(range(1, 10)))
        self.assertEqual(st["budget"]["actual_usd"], 2.0)   # spend accumulates
        self.assertEqual(st["ledger_from"], len(st["ledger"]))
        self.assertTrue(state.start_step(self.job, 1))       # round 2 can run

    def test_new_round_refuses_while_a_step_is_open(self):
        state.start_step(self.job, 1)
        with self.assertRaises(SystemExit):
            _, _ = stderr_of(state.new_round, self.job)

    def test_start_step_unparks_after_escalation(self):
        state.escalate(self.job, "ambiguity", "N001", "two readings", [], ["a", "b"])
        self.assertTrue(state.load(self.job)["position"]["parked"])
        state.start_step(self.job, 2)
        self.assertNotIn("parked", state.load(self.job)["position"])

    # ---- escalations ------------------------------------------------------

    def test_escalation_object_shape_and_parked_position(self):
        rec = state.escalate(self.job, "ambiguity", "N002",
                             "note reads two ways", ["intake/email.txt"],
                             ["tighten to SH002", "tighten to SH003"])
        self.assertEqual(set(rec), {"id", "ts", "trigger", "note_id", "blocked_on",
                                    "evidence", "options", "resolved"})
        self.assertEqual((rec["id"], rec["resolved"]), ("ESC001", False))
        st = state.load(self.job)
        self.assertEqual(st["escalations"], [rec])
        self.assertTrue(st["position"]["parked"])
        second = state.escalate(self.job, "budget", None, "ceiling", [], ["raise"])
        self.assertEqual(second["id"], "ESC002")
        self.assertEqual(len(state.load(self.job)["escalations"]), 2)

    # ---- gates ------------------------------------------------------------

    def test_record_gate_delegated_self_approval(self):
        rec = state.record_gate(self.job, "stills", "agent", "approved",
                                ["stills/edited/SH002_0048_v1.png"],
                                "criterion C1 met: sign reads CLOSED")
        st = state.load(self.job)
        self.assertEqual(st["gates"], [rec])
        self.assertEqual(rec["decider"], "agent")
        self.assertEqual(rec["evidence"], ["stills/edited/SH002_0048_v1.png"])
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                state.record_gate(self.job, "stills", "robot", "approved", [], "x")

    # ---- budget -----------------------------------------------------------

    def test_check_budget_both_sides_of_the_ceiling(self):
        ok, ceiling, actual = state.check_budget(self.job, 5.0)
        self.assertEqual((ok, ceiling, actual), (True, 10.0, 0.0))   # from job.yaml
        state.run_tool(self.job, [sys.executable, "-c", "pass"], cost_usd=8.0)
        ok, ceiling, actual = state.check_budget(self.job, 5.0)
        self.assertEqual((ok, ceiling, actual), (False, 10.0, 8.0))  # would breach
        ok, _, _ = state.check_budget(self.job, 2.0)
        self.assertTrue(ok)                                          # exactly at it
        self.assertEqual(state.load(self.job)["budget"]["ceiling_usd"], 10.0)

    def test_job_config_parses_flat_template(self):
        cfg = state.job_config(self.job)
        self.assertEqual(cfg["job_id"], self.job_id)
        self.assertEqual(cfg["mode"], "standalone")
        self.assertEqual(cfg["gates"], {"lock": "human", "foundation": "human",
                                        "stills": "agent"})

    # ---- partial batch state ---------------------------------------------

    def test_batch_partial_state_round_trips(self):
        state.batch_update(self.job, "round1", "N001", "task-a", "submitted")
        state.batch_update(self.job, "round1", "N002", "task-b", "submitted")
        state.batch_update(self.job, "round1", "N001", None, "verified")
        b = state.batch_state(self.job, "round1")                    # re-read from disk
        self.assertEqual(b["N001"]["task_id"], "task-a")             # kept on update
        self.assertEqual(b["N001"]["status"], "verified")
        self.assertEqual(b["N002"], {"task_id": "task-b", "status": "submitted",
                                     "ts": b["N002"]["ts"]})
        self.assertEqual(state.batch_state(self.job, "nope"), {})
        raw = json.load(open(os.path.join(self.job, "state.json")))
        self.assertEqual(sorted(raw["batches"]["round1"]), ["N001", "N002"])

    # ---- CLI --------------------------------------------------------------

    def test_cli_run_streams_logs_and_propagates_exit(self):
        cp = U.run_tool("state.py", self.job, "run", "--cost-usd", "0.25",
                        "--", sys.executable, "-c", "print('hello')")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("hello", cp.stdout)
        cp = U.run_tool("state.py", self.job, "run", "--",
                        sys.executable, "-c", "import sys; sys.exit(4)")
        self.assertEqual(cp.returncode, 4)                  # child's code propagates
        led = self._ledger()
        self.assertEqual(len(led), 2)
        self.assertEqual([e["exit"] for e in led], [0, 4])
        self.assertEqual(state.load(self.job)["budget"]["actual_usd"], 0.25)

    def test_cli_show_prints_position_budget_and_ledger(self):
        state.start_step(self.job, 1)
        state.complete_step(self.job, 1, "intake normalized")
        state.run_tool(self.job, [sys.executable, "-c", "pass"], cost_usd=2.0)
        state.batch_update(self.job, "round1", "N001", "task-a", "submitted")
        state.escalate(self.job, "budget", "N003", "projected breach", [], ["cut"])
        cp = U.run_tool("state.py", self.job, "show")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        for frag in ("position:", "PARKED", "$2.00 actual of $10.00",
                     "[x] 1 INTAKE - intake normalized", "batch round1: 1 submitted",
                     "OPEN ESC001", "ledger (1 invocations"):
            self.assertIn(frag, cp.stdout)

    def test_cli_requires_a_command_and_a_real_job(self):
        cp = U.run_tool("state.py", self.job, "run")
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "empty_argv")
        cp = U.run_tool("state.py", os.path.join(U.REPO, "jobs", "no-such-job"), "show")
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "no_such_job")

if __name__ == "__main__":
    unittest.main()
