import json, os, shutil, tempfile, unittest
import _util as U

class TestImageEdit(unittest.TestCase):
    def setUp(self):
        U.ensure_fixtures()
        self.tmp = tempfile.mkdtemp()
        self.still = os.path.join(self.tmp, "SQ001-SC001-SH001_first.png")
        shutil.copyfile(U.still(), self.still)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *extra):
        return U.run_tool("image_edit.py", "--still", self.still,
                          "--prompt", "make the jacket red",
                          "--out-dir", self.tmp, "--mock", *extra)

    def test_versioned_output_never_overwrites(self):
        cp1 = self._run()
        self.assertEqual(cp1.returncode, 0, cp1.stderr)
        r1 = json.loads(cp1.stdout)
        self.assertTrue(r1["out"].endswith("SQ001-SC001-SH001_first_v1.png"))
        self.assertEqual(r1["version"], 1)
        cp2 = self._run()
        r2 = json.loads(cp2.stdout)
        self.assertTrue(r2["out"].endswith("_v2.png"))
        self.assertTrue(os.path.exists(r1["out"]))   # v1 untouched
        self.assertTrue(os.path.exists(r2["out"]))

    def test_default_model_is_seedream(self):
        r = json.loads(self._run().stdout)
        self.assertEqual(r["model"], "seedream5pro")

    def test_model_flag_uniform_interface(self):
        for m in ("nanobananapro", "gptimage2"):
            cp = self._run("--model", m)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertEqual(json.loads(cp.stdout)["model"], m)

    def test_missing_still_error(self):
        cp = U.run_tool("image_edit.py", "--still", "nope.png",
                        "--prompt", "x", "--mock")
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "no_such_file")

if __name__ == "__main__":
    unittest.main()
