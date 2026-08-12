import json, os, shutil, tempfile, unittest
import _util as U

class TestEstimateCosts(unittest.TestCase):
    def setUp(self):
        U.ensure_fixtures()
        self.tmp = tempfile.mkdtemp()
        self.notes = os.path.join(self.tmp, "notes.json")
        doc = json.load(open(os.path.join(U.FIX, "notes_valid.json")))
        for n in doc["notes"]:
            n["est_cost"] = None
        json.dump(doc, open(self.notes, "w"), indent=2)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_est_cost_and_totals(self):
        cp = U.run_tool("estimate_costs.py", self.notes)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        doc = json.load(open(self.notes))
        for n in doc["notes"]:
            ec = n["est_cost"]
            self.assertGreater(ec["per_attempt_usd"], 0)
            self.assertGreater(ec["expected_attempts"], 0)
            self.assertAlmostEqual(
                ec["expected_usd"],
                round(ec["per_attempt_usd"] * ec["expected_attempts"], 2),
                delta=0.02)
        self.assertAlmostEqual(
            doc["totals"]["expected_usd"],
            round(sum(n["est_cost"]["expected_usd"] for n in doc["notes"]), 2),
            delta=0.02)
        n = {x["note_id"]: x for x in doc["notes"]}
        # method 1 carries the extra white pass -> costs more per attempt
        # than method 2 on comparable seconds; v2v methods (1,3) bill input
        # video on top. Sanity: m2 (min 4s floor, no video) is cheapest.
        self.assertLess(n["N-002"]["est_cost"]["per_attempt_usd"],
                        n["N-003"]["est_cost"]["per_attempt_usd"])

    def test_min_generation_floor_applies(self):
        # N-002 resolves no shots -> floors at 4s, not 0s
        cp = U.run_tool("estimate_costs.py", self.notes)
        self.assertEqual(cp.returncode, 0)
        doc = json.load(open(self.notes))
        n2 = next(x for x in doc["notes"] if x["note_id"] == "N-002")
        self.assertGreater(n2["est_cost"]["per_attempt_usd"], 0.4)

if __name__ == "__main__":
    unittest.main()
