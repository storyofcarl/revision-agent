import json, os, shutil, unittest, uuid
import _util as U

FIX = U.FIX

class TestRevise(unittest.TestCase):
    def setUp(self):
        self.job_id = f"test-{uuid.uuid4().hex[:8]}"
        self.job_dir = os.path.join(U.REPO, "jobs", self.job_id)
        self.notes_src = os.path.join(FIX, "email.txt")

    def tearDown(self):
        shutil.rmtree(self.job_dir, ignore_errors=True)

    def _run(self, *args):
        return U.run_tool("revise.py", self.job_id, *args)

    def test_scaffolds_standalone_job_per_spec(self):
        cp = self._run("--notes", self.notes_src)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        for d in ("intake", "revision", "refs/_prior", "clips",
                  "stills/extracted", "stills/edited", "stills/approved",
                  "candidates", "verified", "qc", "out", "logs"):
            self.assertTrue(os.path.isdir(os.path.join(self.job_dir, d)), d)
        self.assertTrue(os.path.exists(
            os.path.join(self.job_dir, "intake", "email.txt")))
        state = json.load(open(os.path.join(self.job_dir, "state.json")))
        self.assertEqual(state["mode"], "standalone")
        self.assertEqual(state["position"], {"step": 1, "name": "INTAKE"})
        self.assertEqual(len(state["steps"]), 9)
        self.assertEqual(len(state["invocations"]), 1)
        yaml_text = open(os.path.join(self.job_dir, "job.yaml")).read()
        for line in ("lock: human", "foundation: human", "stills: human"):
            self.assertIn(line, yaml_text)      # conservative defaults
        self.assertIn("agent/OPERATOR.md", cp.stdout)

    def test_occ_native_mode_with_valid_project(self):
        occ = os.path.join(FIX, "occ-project")
        cp = self._run("--notes", self.notes_src, "--occ-project", occ)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        state = json.load(open(os.path.join(self.job_dir, "state.json")))
        self.assertEqual(state["mode"], "occ-native")
        self.assertIn("occ-native", cp.stdout)

    def test_invalid_occ_project_is_error_not_fallback(self):
        cp = self._run("--notes", self.notes_src, "--occ-project", FIX)
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "bad_occ_project")
        self.assertFalse(os.path.exists(self.job_dir))

    def test_resume_appends_never_overwrites(self):
        self._run("--notes", self.notes_src)
        cp = self._run()                         # resume, no notes needed
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("resumed", cp.stdout)
        state = json.load(open(os.path.join(self.job_dir, "state.json")))
        self.assertEqual(len(state["invocations"]), 2)

    def test_fresh_round_requires_notes(self):
        cp = self._run()
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "no_notes")

    def test_config_overrides_at_scaffold(self):
        cp = self._run("--notes", self.notes_src, "--config",
                       "budget_ceiling_usd=25.0", "gates.stills=agent")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        yaml_text = open(os.path.join(self.job_dir, "job.yaml")).read()
        self.assertIn("budget_ceiling_usd: 25.0", yaml_text)
        self.assertIn("stills: agent", yaml_text)
        self.assertIn("lock: human", yaml_text)

    def test_config_after_scaffold_rejected(self):
        self._run("--notes", self.notes_src)
        cp = self._run("--config", "budget_ceiling_usd=99")
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "config_after_scaffold")

if __name__ == "__main__":
    unittest.main()
