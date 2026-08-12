import json, os, unittest
import _util as U

FIX = U.FIX

class TestIntakeNormalize(unittest.TestCase):
    def test_email_splits_and_extracts_timecodes(self):
        cp = U.run_tool("intake_normalize.py", "--source", "email",
                        os.path.join(FIX, "email.txt"), "--fps", "24")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        notes = json.loads(cp.stdout)["notes"]
        self.assertGreaterEqual(len(notes), 3)
        # every note: verbatim raw_text, judgment fields left empty
        for n in notes:
            self.assertTrue(n["raw_text"])
            self.assertIsNone(n["interpretation"])
            self.assertIsNone(n["scope"])
            self.assertIsNone(n["method"])
            self.assertEqual(n["acceptance_criteria"], [])
            self.assertEqual(n["source"]["type"], "email")
        by_text = {n["raw_text"]: n for n in notes}
        jacket = next(n for t, n in by_text.items() if "jacket" in t)
        self.assertEqual(jacket["timecode"]["in"], "00:00:05:00")
        self.assertEqual(jacket["timecode"]["fps_base"], 24)
        sign = next(n for t, n in by_text.items() if "sign" in t)
        self.assertEqual(sign["timecode"]["in"], "00:00:12:00")
        self.assertEqual(sign["timecode"]["out"], "00:00:15:00")
        self.assertIn("Dana Client", jacket["source"]["author"])

    def test_email_fps_null_when_not_given(self):
        cp = U.run_tool("intake_normalize.py", "--source", "email",
                        os.path.join(FIX, "email.txt"))
        notes = json.loads(cp.stdout)["notes"]
        jacket = next(n for n in notes if "jacket" in n["raw_text"])
        self.assertIsNone(jacket["timecode"]["fps_base"])

    def test_sheet_mapping_and_shot_column(self):
        cp = U.run_tool("intake_normalize.py", "--source", "sheet",
                        os.path.join(FIX, "sheet.csv"), "--fps", "24")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        notes = json.loads(cp.stdout)["notes"]
        self.assertEqual(len(notes), 3)
        self.assertEqual(notes[0]["resolved_shots"], ["SQ001-SC001-SH001"])
        self.assertEqual(notes[0]["timecode"]["in"], "00:00:05:00")
        self.assertEqual(notes[1]["timecode"], None)
        self.assertEqual(notes[2]["source"]["author"], "Sam")
        self.assertEqual(notes[2]["timecode"]["in"], "00:01:02:12")

    def test_sheet_ambiguous_mapping_is_error_not_guess(self):
        cp = U.run_tool("intake_normalize.py", "--source", "sheet",
                        os.path.join(FIX, "sheet_ambiguous.csv"))
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "ambiguous_mapping")

    def test_frameio_json_merges_replies(self):
        cp = U.run_tool("intake_normalize.py", "--source", "frameio",
                        os.path.join(FIX, "frameio.json"))
        self.assertEqual(cp.returncode, 0, cp.stderr)
        notes = json.loads(cp.stdout)["notes"]
        self.assertEqual(len(notes), 2)          # reply merged into parent
        self.assertIn("[reply]", notes[0]["raw_text"])
        self.assertIn("deep red", notes[0]["raw_text"])
        self.assertEqual(notes[0]["source"]["ref"], "c1")
        self.assertEqual(notes[0]["timecode"]["fps_base"], 24)
        self.assertEqual(notes[1]["timecode"]["out"], "00:00:15:00")

    def test_missing_file_error(self):
        cp = U.run_tool("intake_normalize.py", "--source", "email", "nope.txt")
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "no_such_file")

if __name__ == "__main__":
    unittest.main()
