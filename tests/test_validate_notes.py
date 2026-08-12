import os, unittest
import _util as U

class TestValidateNotes(unittest.TestCase):
    def test_valid_file_passes(self):
        cp = U.run_tool("validate_notes.py",
                        os.path.join(U.FIX, "notes_valid.json"))
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("OK", cp.stdout)

    def test_invalid_file_reports_every_error_class(self):
        cp = U.run_tool("validate_notes.py",
                        os.path.join(U.FIX, "notes_invalid.json"))
        self.assertNotEqual(cp.returncode, 0)
        out = cp.stdout
        self.assertIn("missing field 'interpretation'", out)   # missing field
        self.assertIn("duplicate note_id", out)                # duplicate id
        self.assertIn("scope must be foundational|local", out) # bad scope
        self.assertIn("method must be 1-4", out)               # bad method
        self.assertIn("requires assets_touched", out)          # foundational w/o assets
        self.assertIn("no resolved_shots", out)                # local w/o shots
        self.assertIn("no acceptance criteria", out)           # empty criteria
        self.assertIn("not a question", out)                   # non-question
        self.assertIn("not observable", out)                   # vague criterion

if __name__ == "__main__":
    unittest.main()
