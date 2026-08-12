import json, os, shutil, sys, tempfile, unittest
import _util as U

sys.path.insert(0, U.TOOLS)
import batch_generate as bg   # noqa: E402


class TestDryRun(unittest.TestCase):
    def setUp(self):
        U.ensure_fixtures()
        self.job = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.job, "revision", "payloads"))
        doc = json.load(open(os.path.join(U.FIX, "notes_valid.json")))
        doc["notes"][0]["status"] = "stills_approved"
        self.notes = os.path.join(self.job, "notes.json")
        json.dump(doc, open(self.notes, "w"))
        payload = {"model_key": "seedance_pro", "prompt": "test prompt",
                   "ref_video": None, "first_frame": None, "last_frame": None,
                   "reference_images": [], "ref_audio": None, "ratio": "16:9",
                   "resolution": "720p", "duration": 5.0,
                   "generate_audio": True}
        json.dump(payload, open(os.path.join(
            self.job, "revision", "payloads", "N-001.json"), "w"))

    def tearDown(self):
        shutil.rmtree(self.job, ignore_errors=True)

    def test_dry_run_plans_and_spends_nothing(self):
        cp = U.run_tool("batch_generate.py", self.job, self.notes, "--dry-run")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("N-001", cp.stdout)
        self.assertIn("nothing submitted, nothing spent", cp.stdout)
        # notes untouched by a dry run
        doc = json.load(open(self.notes))
        self.assertEqual(doc["notes"][0]["status"], "stills_approved")

    def test_missing_payload_is_error(self):
        os.unlink(os.path.join(self.job, "revision", "payloads", "N-001.json"))
        cp = U.run_tool("batch_generate.py", self.job, self.notes, "--dry-run")
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("missing payload spec", cp.stderr + cp.stdout)

    def test_nothing_queued_is_error(self):
        doc = json.load(open(self.notes))
        doc["notes"][0]["status"] = "pending"
        json.dump(doc, open(self.notes, "w"))
        cp = U.run_tool("batch_generate.py", self.job, self.notes, "--dry-run")
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("Nothing queued", cp.stderr + cp.stdout)


class TestProbeQuota(unittest.TestCase):
    def test_ramp_reports_ceiling_at_mock_429(self):
        CEILING = 4
        state = {"n": 0, "in_flight": set()}

        def submit():
            if len(state["in_flight"]) >= CEILING:
                raise bg.QuotaExceeded("429 too many concurrent tasks")
            state["n"] += 1
            tid = f"t{state['n']}"
            state["in_flight"].add(tid)
            return tid

        def status(tid):
            return "running"     # nothing drains during the ramp

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "quota-probe.json")
            report = bg.probe_quota(20, submit_fn=submit, status_fn=status,
                                    sleep_fn=lambda s: None, out_path=out)
            self.assertEqual(report["ceiling"], CEILING)
            self.assertTrue(report["refused_by_api"])
            self.assertEqual(report["tasks_submitted"], CEILING)
            on_disk = json.load(open(out))
            self.assertEqual(on_disk["ceiling"], CEILING)

    def test_max_tasks_caps_unrefused_ramp(self):
        state = {"n": 0}

        def submit():
            state["n"] += 1
            return f"t{state['n']}"

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "quota-probe.json")
            report = bg.probe_quota(3, submit_fn=submit,
                                    status_fn=lambda t: "running",
                                    sleep_fn=lambda s: None, out_path=out)
            self.assertEqual(report["tasks_submitted"], 3)
            self.assertFalse(report["refused_by_api"])

    def test_cli_refuses_without_confirm(self):
        cp = U.run_tool("batch_generate.py", "--probe-quota", "--max-tasks", "2")
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("Refusing to submit without --confirm",
                      cp.stderr + cp.stdout)
        self.assertIn("estimated cost", cp.stdout)   # cost printed pre-refusal


if __name__ == "__main__":
    unittest.main()
