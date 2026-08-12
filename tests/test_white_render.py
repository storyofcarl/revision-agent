import json, os, unittest
import _util as U

MANIFEST = os.path.join(U.GEN, "shots.json")

def clip(sid):
    return os.path.join(U.GEN, f"{sid}.mp4")

class TestWhiteRender(unittest.TestCase):
    def setUp(self):
        U.ensure_fixtures()

    def test_dry_run_plans_without_spend(self):
        cp = U.run_tool("white_render.py", "--clips",
                        clip("SQ001-SC001-SH001"), "--manifest", MANIFEST,
                        "--dry-run")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        plan = json.loads(cp.stdout)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["plan"][0]["shots"], ["SQ001-SC001-SH001"])
        self.assertGreater(plan["est_total_usd"], 0)

    def test_sub4s_bundles_with_noted_neighbor(self):
        # SH002 (2.5s) with SH003 also targeted: bundle prefers the neighbor
        # already receiving a note
        cp = U.run_tool("white_render.py", "--clips",
                        clip("SQ001-SC001-SH002"), clip("SQ001-SC001-SH003"),
                        "--manifest", MANIFEST, "--dry-run")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        plan = json.loads(cp.stdout)["plan"]
        self.assertEqual(len(plan), 1)          # one bundle covers both
        self.assertEqual(set(plan[0]["shots"]),
                         {"SQ001-SC001-SH002", "SQ001-SC001-SH003"})
        self.assertGreaterEqual(plan[0]["duration_s"], 4.0)

    def test_sub4s_never_bundles_across_scene_when_in_scene_exists(self):
        # SH002 alone: neighbors are SH001/SH003 (in-scene). SH004 is the
        # next scene and must not be chosen.
        cp = U.run_tool("white_render.py", "--clips",
                        clip("SQ001-SC001-SH002"), "--manifest", MANIFEST,
                        "--dry-run")
        plan = json.loads(cp.stdout)["plan"]
        self.assertNotIn("SQ001-SC002-SH004", plan[0]["shots"])
        self.assertIn("SQ001-SC001-SH002", plan[0]["shots"])

    def test_bundle_recorded_with_per_shot_spans(self):
        cp = U.run_tool("white_render.py", "--clips",
                        clip("SQ001-SC001-SH002"), clip("SQ001-SC001-SH003"),
                        "--manifest", MANIFEST, "--dry-run")
        spans = json.loads(cp.stdout)["plan"][0]["spans"]
        self.assertEqual(spans[0]["offset_s"], 0.0)
        self.assertAlmostEqual(spans[1]["offset_s"], spans[0]["duration_s"])

    def test_sub4s_without_partner_is_error(self):
        solo = [{"shot_id": "SQ001-SC001-SH002", "duration_s": 2.5}]
        m = os.path.join(U.GEN, "solo_manifest.json")
        json.dump(solo, open(m, "w"))
        cp = U.run_tool("white_render.py", "--clips",
                        clip("SQ001-SC001-SH002"), "--manifest", m,
                        "--dry-run")
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "no_bundle_partner")

    def test_sub4s_without_manifest_is_error(self):
        cp = U.run_tool("white_render.py", "--clips",
                        clip("SQ001-SC001-SH002"), "--dry-run")
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "no_bundle_partner")

if __name__ == "__main__":
    unittest.main()
