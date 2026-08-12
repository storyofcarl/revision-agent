import json, os, shutil, tempfile, unittest
import _util as U

class TestBuildChangelog(unittest.TestCase):
    def test_renders_statuses_including_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = json.load(open(os.path.join(U.FIX, "notes_valid.json")))
            doc["notes"][0]["status"] = "verified"
            doc["notes"][1]["status"] = "escalated"
            doc["notes"][2]["status"] = "failed"
            notes = os.path.join(tmp, "notes.json")
            json.dump(doc, open(notes, "w"))
            cp = U.run_tool("build_changelog.py", notes)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            out = cp.stdout
            self.assertIn("Revision round 1", out)
            self.assertIn("N-001 — Addressed", out)
            self.assertIn("ESCALATED", out)
            self.assertIn("NOT RESOLVED", out)      # stated, not hidden
            self.assertIn("accept best version, cut around, or re-scope", out)
            # verbatim raw text quoted per note
            self.assertIn("jacket should be red", out)

if __name__ == "__main__":
    unittest.main()
