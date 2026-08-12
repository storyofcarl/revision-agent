import json, os, shutil, subprocess, tempfile, unittest
import _util as U

# 3 visually distinct scenes so scene detection has real cuts to find
SCENES = [("testsrc2", 3.0), ("red", 2.0), ("bars", 4.0)]
MASTER = "master_3scene.mp4"
TOTAL = sum(d for _, d in SCENES)

FILTERS = {"testsrc2": "testsrc2=duration={d}:size={s}:rate={r}",
           "red": "color=c=red:duration={d}:size={s}:rate={r}",
           "bars": "smptebars=duration={d}:size={s}:rate={r}"}

def build_master():
    """Concat three distinct lavfi scenes into one assembled cut (cached).
    Concatenated in the filter graph, not by the demuxer: a single encode
    keeps the filter chain from being reinitialized at a segment join,
    which is what a real master looks like to scene detection."""
    path = os.path.join(U.GEN, MASTER)
    if os.path.exists(path):
        return path
    args, chain = [], ""
    for i, (name, dur) in enumerate(SCENES):
        args += ["-f", "lavfi", "-i",
                 FILTERS[name].format(d=dur, s=U.SIZE, r=U.RATE)]
        chain += f"[{i}:v]"
    U._ff(*args, "-filter_complex", f"{chain}concat=n={len(SCENES)}:v=1:a=0",
          "-pix_fmt", "yuv420p", "-preset", "ultrafast", path)
    return path

def probe(path):
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                          "-show_format", path], capture_output=True, text=True,
                         check=True)
    return float(json.loads(out.stdout)["format"]["duration"])

class TestShotSplit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        U.ensure_fixtures()
        cls.master = build_master()
        cls.master_dur = probe(cls.master)

    def setUp(self):
        self.job = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.job, ignore_errors=True)

    def shots(self):
        return json.load(open(os.path.join(self.job, "shots.json")))

    def test_scene_detection_finds_three_shots(self):
        cp = U.run_tool("shot_split.py", "--cut", self.master,
                        "--job-dir", self.job)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        shots = self.shots()
        self.assertEqual([s["shot_id"] for s in shots],
                         ["SQ001-SC001-SH010", "SQ001-SC001-SH020",
                          "SQ001-SC001-SH030"])
        for shot, (_, expected) in zip(shots, SCENES):
            self.assertAlmostEqual(shot["duration_s"], expected, delta=0.3,
                                   msg=f"{shot['shot_id']} {shot}")
        for shot in shots:
            clip = os.path.join(self.job, "clips", shot["shot_id"] + ".mp4")
            self.assertTrue(os.path.exists(clip))
            self.assertAlmostEqual(probe(clip), shot["duration_s"], delta=0.1)

    def test_durations_sum_to_master(self):
        cp = U.run_tool("shot_split.py", "--cut", self.master,
                        "--job-dir", self.job)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertAlmostEqual(sum(s["duration_s"] for s in self.shots()),
                               self.master_dur, delta=0.1)

    def test_edl_mode_gives_exact_boundaries(self):
        edl = os.path.join(self.job, "cuts.edl")
        with open(edl, "w") as fh:
            fh.write("# operator boundaries\n0\n00:00:03\n00:00:05:00\n")
        cp = U.run_tool("shot_split.py", "--cut", self.master,
                        "--job-dir", self.job, "--edl", edl,
                        "--prefix", "SQ002-SC004")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        shots = self.shots()
        self.assertEqual([s["shot_id"] for s in shots],
                         ["SQ002-SC004-SH010", "SQ002-SC004-SH020",
                          "SQ002-SC004-SH030"])
        self.assertEqual([s["start_s"] for s in shots], [0.0, 3.0, 5.0])
        self.assertEqual([s["duration_s"] for s in shots[:2]], [3.0, 2.0])
        self.assertAlmostEqual(sum(s["duration_s"] for s in shots),
                               self.master_dur, delta=0.1)

    def test_dry_run_writes_nothing(self):
        cp = U.run_tool("shot_split.py", "--cut", self.master,
                        "--job-dir", self.job, "--dry-run")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        plan = json.loads(cp.stdout)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(len(plan["shots"]), 3)
        self.assertEqual(os.listdir(self.job), [])

    def test_refuses_to_overwrite_live_lineup(self):
        os.makedirs(os.path.join(self.job, "clips"))
        shutil.copyfile(os.path.join(U.GEN, "SQ001-SC001-SH001.mp4"),
                        os.path.join(self.job, "clips", "SQ001-SC001-SH010.mp4"))
        cp = U.run_tool("shot_split.py", "--cut", self.master,
                        "--job-dir", self.job)
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "lineup_exists")
        self.assertFalse(os.path.exists(os.path.join(self.job, "shots.json")))
        cp = U.run_tool("shot_split.py", "--cut", self.master,
                        "--job-dir", self.job, "--force")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(len(self.shots()), 3)

    def test_missing_cut_is_a_json_error(self):
        cp = U.run_tool("shot_split.py", "--cut", os.path.join(self.job, "nope.mp4"),
                        "--job-dir", self.job)
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "no_such_file")

    def test_bad_edl_timecode_is_a_json_error(self):
        edl = os.path.join(self.job, "bad.edl")
        open(edl, "w").write("00:00:03\nhalfway\n")
        cp = U.run_tool("shot_split.py", "--cut", self.master,
                        "--job-dir", self.job, "--edl", edl)
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "bad_timecode")

    def test_min_shot_merges_close_boundaries(self):
        cp = U.run_tool("shot_split.py", "--cut", self.master,
                        "--job-dir", self.job, "--min-shot-s", "6")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        shots = self.shots()
        self.assertLess(len(shots), 3)
        self.assertAlmostEqual(sum(s["duration_s"] for s in shots),
                               self.master_dur, delta=0.1)

if __name__ == "__main__":
    unittest.main()
