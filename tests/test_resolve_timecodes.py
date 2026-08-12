import json, os, shutil, tempfile, unittest
import _util as U

class TestResolveTimecodes(unittest.TestCase):
    def setUp(self):
        U.ensure_fixtures()
        self.tmp = tempfile.mkdtemp()
        self.notes = os.path.join(self.tmp, "notes.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, mutate=None):
        doc = json.load(open(os.path.join(U.FIX, "notes_valid.json")))
        for n in doc["notes"]:
            n["resolved_shots"] = []          # force fresh resolution
        if mutate:
            mutate(doc)
        json.dump(doc, open(self.notes, "w"), indent=2)
        return doc

    def test_resolves_single_and_multi_shot_ranges(self):
        self._write()
        cp = U.run_tool("resolve_timecodes.py", U.GEN, self.notes)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        doc = json.load(open(self.notes))
        n = {x["note_id"]: x for x in doc["notes"]}
        # 1s-3s sits inside SH001 (0-5s)
        self.assertEqual(n["N-001"]["resolved_shots"], ["SQ001-SC001-SH001"])
        # 6s-8s spans SH002 (5-7.5) and SH003 (7.5-11.5)
        self.assertEqual(n["N-003"]["resolved_shots"],
                         ["SQ001-SC001-SH002", "SQ001-SC001-SH003"])
        # asset note untouched
        self.assertEqual(n["N-002"]["resolved_shots"], [])

    def test_missing_fps_flagged_not_assumed(self):
        self._write(lambda d: d["notes"][0]["timecode"].pop("fps_base"))
        cp = U.run_tool("resolve_timecodes.py", U.GEN, self.notes)
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("fps_base missing", cp.stdout)

    def test_out_of_range_flagged(self):
        def mutate(d):
            d["notes"][0]["timecode"]["in"] = "00:10:00:00"
            d["notes"][0]["timecode"]["out"] = "00:10:05:00"
        self._write(mutate)
        cp = U.run_tool("resolve_timecodes.py", U.GEN, self.notes)
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("wrong cut version or wrong fps base", cp.stdout)

if __name__ == "__main__":
    unittest.main()
