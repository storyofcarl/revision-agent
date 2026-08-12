import os, shutil, tempfile, unittest
import _util as U

class TestExtractStills(unittest.TestCase):
    def setUp(self):
        U.ensure_fixtures()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_and_last_always_written(self):
        clip = os.path.join(U.GEN, "SQ001-SC001-SH003.mp4")
        cp = U.run_tool("extract_stills.py", clip, self.tmp)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        files = sorted(os.listdir(self.tmp))
        self.assertIn("first.png", files)
        self.assertIn("last.png", files)
        self.assertEqual(len(files), 2)

    def test_every_adds_interior_stills(self):
        clip = os.path.join(U.GEN, "SQ001-SC001-SH001.mp4")   # 5s
        cp = U.run_tool("extract_stills.py", clip, self.tmp, "--every", "1")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        files = sorted(os.listdir(self.tmp))
        interior = [f for f in files if f.startswith("t")]
        self.assertGreaterEqual(len(interior), 3)
        self.assertIn("first.png", files)
        self.assertIn("last.png", files)

if __name__ == "__main__":
    unittest.main()
