import json, os, shutil, subprocess, tempfile, unittest
import _util as U

# the generated lineup: 5.0 + 2.5 + 4.0 + 4.0
SHOTS = ["SQ001-SC001-SH001", "SQ001-SC001-SH002",
         "SQ001-SC001-SH003", "SQ001-SC002-SH004"]
LINEUP_S = 15.5
SWAP = "candidates/conformed/N-001.mp4"     # 2.5s, replaces SH002
INSERT = "candidates/conformed/N-014.mp4"   # 2.5s, an arbitrary insert

def audio_master(dur):
    """A fake mixed master: black picture + a sine 'song' with a silent
    tail (anullsrc), cut to exactly `dur`. Only its audio is ever used."""
    name = f"audio_master_{dur:g}s.mp4"
    path = os.path.join(U.GEN, name)
    if not os.path.exists(path):
        U._ff("-f", "lavfi", "-i",
              f"color=c=black:size=160x90:rate={U.RATE}:duration={dur}",
              "-f", "lavfi", "-i",
              f"sine=frequency=440:duration={max(dur - 1, 0.5)}:sample_rate=48000",
              "-f", "lavfi", "-t", "1", "-i", "anullsrc=r=48000:cl=mono",
              "-filter_complex", "[1:a][2:a]concat=n=2:v=0:a=1[a]",
              "-map", "0:v", "-map", "[a]", "-t", f"{dur}",
              "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
              "-c:a", "aac", path)
    return path

def probe(path):
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                          "-show_format", path], capture_output=True, text=True,
                         check=True)
    return float(json.loads(out.stdout)["format"]["duration"])

class TestAssembleCut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        U.ensure_fixtures()
        cls.master_full = audio_master(LINEUP_S)

    def setUp(self):
        self.job = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.job, "clips"))
        os.makedirs(os.path.join(self.job, "candidates", "conformed"))
        for s in SHOTS:
            shutil.copyfile(os.path.join(U.GEN, s + ".mp4"),
                            os.path.join(self.job, "clips", s + ".mp4"))
        shutil.copyfile(os.path.join(U.GEN, "shots.json"),
                        os.path.join(self.job, "shots.json"))
        # both candidates are 2.5s so a swap keeps the lineup runtime honest
        for rel in (SWAP, INSERT):
            shutil.copyfile(os.path.join(U.GEN, "SQ001-SC001-SH002.mp4"),
                            os.path.join(self.job, rel))
        self.out = os.path.join(self.job, "revision", "cut_v2.mp4")

    def tearDown(self):
        shutil.rmtree(self.job, ignore_errors=True)

    def write_plan(self, master, **over):
        # the master lives in the job, as a real round's does
        shutil.copyfile(master, os.path.join(self.job, "master.mp4"))
        plan = {"width": 320, "height": 180, "fps": 24,
                "audio_master": "master.mp4"}
        plan.update(over)
        path = os.path.join(self.job, "assembly.json")
        json.dump(plan, open(path, "w"), indent=2)
        return path

    def run_cut(self, plan, *extra):
        return U.run_tool("assemble_cut.py", self.job, "--out", self.out,
                          "--plan", plan, *extra)

    def segments(self):
        d = os.path.join(self.job, "revision", "assembly_segments")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def test_default_order_with_swap(self):
        plan = self.write_plan(self.master_full,
                               swaps={"SQ001-SC001-SH002": SWAP})
        cp = self.run_cut(plan)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("OK", cp.stdout)
        self.assertTrue(os.path.exists(self.out))
        self.assertAlmostEqual(probe(self.out), LINEUP_S, delta=0.25)
        self.assertEqual(len(self.segments()), 4)
        log = [json.loads(l) for l in
               open(os.path.join(self.job, "logs", "tools.jsonl"))]
        self.assertEqual(log[-1]["swapped"], ["SQ001-SC001-SH002"])
        # PROTECTED: the swap source is an input, candidates/ is untouched
        self.assertEqual(sorted(os.listdir(
            os.path.join(self.job, "candidates", "conformed"))),
            ["N-001.mp4", "N-014.mp4"])
        # re-running re-uses the segment cache instead of re-encoding
        cached = self.segments()
        cp = self.run_cut(plan)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("0 encoded, 4 cached", cp.stdout)
        self.assertEqual(self.segments(), cached)

    def test_edl_with_insert_and_trim(self):
        edl = [{"shot": "SQ001-SC001-SH001"},          # 5.0
               {"file": INSERT},                       # 2.5
               {"shot": "SQ001-SC001-SH003", "trim": [0, 0.41]}]
        expected = 5.0 + 2.5 + 0.41
        plan = self.write_plan(audio_master(expected), edl=edl)
        cp = self.run_cut(plan)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertAlmostEqual(probe(self.out), expected, delta=0.25)
        self.assertEqual(len(self.segments()), 3)

    def test_dry_run_writes_nothing_and_reports_timeline(self):
        plan = self.write_plan(self.master_full,
                               swaps={"SQ001-SC001-SH002": SWAP})
        cp = self.run_cut(plan, "--dry-run")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        doc = json.loads(cp.stdout)
        self.assertTrue(doc["dry_run"])
        self.assertEqual(doc["source"], "shots.json")
        self.assertEqual([s["source"] for s in doc["segments"]],
                         ["clips/SQ001-SC001-SH001.mp4", SWAP,
                          "clips/SQ001-SC001-SH003.mp4",
                          "clips/SQ001-SC002-SH004.mp4"])
        self.assertEqual([s["swapped"] for s in doc["segments"]],
                         [False, True, False, False])
        self.assertEqual([s["running_total_s"] for s in doc["segments"]],
                         [5.0, 7.5, 11.5, 15.5])
        self.assertAlmostEqual(doc["expected_total_s"], LINEUP_S, delta=0.05)
        self.assertTrue(doc["within_tolerance"])
        self.assertFalse(os.path.exists(self.out))
        self.assertFalse(os.path.exists(os.path.join(self.job, "revision")))
        self.assertFalse(os.path.exists(os.path.join(self.job, "logs")))

    def test_audio_video_mismatch_fails(self):
        plan = self.write_plan(audio_master(10))     # lineup is 15.5s
        cp = self.run_cut(plan)
        self.assertNotEqual(cp.returncode, 0)
        err = U.stderr_error(cp)
        self.assertEqual(err["error"], "av_duration_mismatch")
        self.assertAlmostEqual(err["actual_video_s"], LINEUP_S, delta=0.25)
        self.assertAlmostEqual(err["expected_audio_s"], 10, delta=0.25)
        self.assertFalse(os.path.exists(self.out))

    def test_missing_swap_file_fails(self):
        plan = self.write_plan(
            self.master_full,
            swaps={"SQ001-SC001-SH002": "candidates/conformed/N-099.mp4"})
        cp = self.run_cut(plan)
        self.assertNotEqual(cp.returncode, 0)
        err = U.stderr_error(cp)
        self.assertEqual(err["error"], "no_such_file")
        self.assertIn("SQ001-SC001-SH002", err["message"])
        self.assertFalse(os.path.exists(self.out))

    def test_refuses_to_write_into_candidates(self):
        plan = self.write_plan(self.master_full)
        self.out = os.path.join(self.job, "candidates", "cut_v2.mp4")
        cp = self.run_cut(plan)
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "protected_output")
        self.assertFalse(os.path.exists(self.out))

if __name__ == "__main__":
    unittest.main()
