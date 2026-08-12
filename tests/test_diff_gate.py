import json, os, shutil, tempfile, unittest
import _util as U

# the red box is drawn at x=224,y=126 (64x36) on 320x180 => normalized
# bbox ~ (0.70, 0.70, 0.20, 0.20); authorize slightly larger for encode bleed
AUTH = {"authorized": [{"type": "bbox", "data": "0.66,0.66,0.28,0.28"}]}

class TestDiffGate(unittest.TestCase):
    def setUp(self):
        U.ensure_fixtures()
        self.tmp = tempfile.mkdtemp()
        self.orig = os.path.join(U.GEN, "SQ001-SC001-SH001.mp4")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _spec(self, spec):
        p = os.path.join(self.tmp, "regions.json")
        json.dump(spec, open(p, "w"))
        return p

    def _run(self, candidate, spec):
        return U.run_tool("diff_gate.py", "--original", self.orig,
                          "--candidate", os.path.join(U.GEN, candidate),
                          "--regions", self._spec(spec))

    def test_authorized_change_passes(self):
        cp = self._run("SH001_authorized.mp4", AUTH)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(json.loads(cp.stdout)["verdict"], "PASS")

    def test_same_change_outside_region_fails(self):
        cp = self._run("SH001_unauthorized.mp4", AUTH)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        res = json.loads(cp.stdout)
        self.assertEqual(res["verdict"], "FAIL")
        self.assertIn("collateral", res["fail_reason"])

    def test_identical_clip_passes_with_empty_authorization(self):
        cp = self._run("SQ001-SC001-SH001.mp4", {"authorized": []})
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)

    def test_runtime_mismatch_fails(self):
        cp = self._run("SH001_short.mp4", {"authorized": [{"type": "fullframe"}]})
        self.assertEqual(cp.returncode, 1)
        self.assertIn("runtime mismatch", json.loads(cp.stdout)["fail_reason"])

    def test_bad_spec_is_usage_error(self):
        cp = self._run("SH001_authorized.mp4", {"pixel_delta": 20})
        self.assertEqual(cp.returncode, 2)

if __name__ == "__main__":
    unittest.main()
