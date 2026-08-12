import json, os, shutil, tempfile, unittest
import _util as U

SHOTS = [{"shot_id": "SQ001-SC001-SH001", "duration_s": 5.0},
         {"shot_id": "SQ001-SC001-SH003", "duration_s": 4.0}]

class TestReassemble(unittest.TestCase):
    def setUp(self):
        U.ensure_fixtures()
        self.job = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.job, "clips"))
        json.dump(SHOTS, open(os.path.join(self.job, "shots.json"), "w"))
        for s in SHOTS:
            shutil.copyfile(os.path.join(U.GEN, s["shot_id"] + ".mp4"),
                            os.path.join(self.job, "clips", s["shot_id"] + ".mp4"))
        self.out = os.path.join(self.job, "cut_v1.mp4")

    def tearDown(self):
        shutil.rmtree(self.job, ignore_errors=True)

    def test_assembles_and_verifies_runtime(self):
        cp = U.run_tool("reassemble.py", self.job, self.out)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertTrue(os.path.exists(self.out))
        self.assertIn("OK", cp.stdout)

    def test_refuses_to_stitch_gaps(self):
        os.unlink(os.path.join(self.job, "clips", "SQ001-SC001-SH003.mp4"))
        cp = U.run_tool("reassemble.py", self.job, self.out)
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("never stitch gaps", cp.stdout)
        self.assertFalse(os.path.exists(self.out))

    def test_wrong_length_clip_fails_runtime_check(self):
        # swap SH003 for the 2.5s clip: assembly runs but runtime mismatches
        shutil.copyfile(os.path.join(U.GEN, "SQ001-SC001-SH002.mp4"),
                        os.path.join(self.job, "clips", "SQ001-SC001-SH003.mp4"))
        cp = U.run_tool("reassemble.py", self.job, self.out)
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("FAIL", cp.stdout)

if __name__ == "__main__":
    unittest.main()
