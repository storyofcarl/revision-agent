import json, os, shutil, unittest, uuid
import _util as U

FIX = U.FIX
TS = "2026-08-12T13:00:00Z"

def _gates():
    return [
        {"ts": TS, "gate": "lock", "decider": "human", "outcome": "approved",
         "evidence": ["revision/notes.json"], "rationale": "notes table approved"},
        {"ts": TS, "gate": "stills", "decider": "agent", "outcome": "approved",
         "evidence": ["stills/edited/SQ001-SC001-SH001_0000_v2.png",
                      "stills/extracted/SQ001-SC001-SH001_0000.png"],
         "rationale": "N-001-c1 satisfied: jacket red in both key stills",
         "note_id": "N-001"},
        # gate-string attribution (no explicit note_id key)
        {"ts": TS, "gate": "stills:N-003", "decider": "human",
         "outcome": "approved",
         "evidence": ["stills/edited/SQ001-SC001-SH002_0000_v1.png"],
         "rationale": "sign reads OPEN"},
    ]

def _ledger():
    return [
        {"ts": TS, "argv": ["image_edit.py", "--note", "N-001", "--model",
                            "seedream5pro"], "exit": 0, "duration_s": 3.2,
         "cost_usd": 0.04},
        {"ts": TS, "argv": ["batch_generate.py", "--notes",
                            "revision/notes.json", "--only", "N-001"],
         "exit": 0, "duration_s": 91.0, "cost_usd": 1.90},
        {"ts": TS, "argv": ["batch_generate.py", "--only", "N-002"],
         "exit": 0, "duration_s": 60.0, "cost_usd": 0.60},
        {"ts": TS, "argv": ["diff_gate.py", "--original", "clips/a.mp4"],
         "exit": 1, "duration_s": 1.1, "cost_usd": 0.0},
        {"ts": TS, "argv": ["reassemble.py", "--out", "out/cut_v2.mp4"],
         "exit": 0, "duration_s": 4.0, "cost_usd": 0.0},
    ]

ESCALATION = {
    "ts": TS, "trigger": "repair_exhaustion", "note_id": "N-003",
    "blocked_on": "2 cycles on SQ001-SC001-SH002 without a passing candidate",
    "evidence": ["candidates/SQ001-SC001-SH002_v2.mp4",
                 "qc/round1/N-003/verdict_card.json"],
    "options": ["accept best version", "cut around", "re-scope the note"],
}

class TestBuildRoundReport(unittest.TestCase):
    def setUp(self):
        self.job_id = f"test-{uuid.uuid4().hex[:8]}"
        self.job_dir = os.path.join(U.REPO, "jobs", self.job_id)
        cp = U.run_tool("revise.py", self.job_id, "--notes",
                        os.path.join(FIX, "email.txt"), "--config",
                        "gates.stills=agent", "budget_ceiling_usd=25.0")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.report = os.path.join(self.job_dir, "out", "round_report.json")
        self.seed()

    def tearDown(self):
        shutil.rmtree(self.job_dir, ignore_errors=True)

    def seed(self, escalated=True):
        """Seeded notes.json + synthetic state.json entries: two verified
        notes, an agent-delegated stills self-approval with evidence, a
        priced ledger, and (optionally) a parked escalation."""
        doc = json.load(open(os.path.join(FIX, "notes_valid.json")))
        by_id = {n["note_id"]: n for n in doc["notes"]}
        by_id["N-001"]["status"] = "verified"
        by_id["N-001"]["lineage"].update(
            {"revision_cycle": 2,
             "candidates": [
                 {"path": "candidates/SQ001-SC001-SH001_v1.mp4", "ts": TS,
                  "verdict": "FAIL", "qc_ref": "qc/round1/N-001/v1.json"},
                 {"path": "candidates/SQ001-SC001-SH001_v2.mp4", "ts": TS,
                  "verdict": "PASS", "qc_ref": "qc/round1/N-001/v2.json"}],
             "qc_refs": ["qc/round1/N-001"]})
        by_id["N-002"]["status"] = "done"
        by_id["N-002"]["lineage"].update(
            {"revision_cycle": 1,
             "candidates": [{"path": "candidates/crowd_v1.mp4", "ts": TS,
                             "verdict": "PASS"}]})
        by_id["N-003"]["status"] = "escalated" if escalated else "done"
        by_id["N-003"]["lineage"]["revision_cycle"] = 2 if escalated else 1
        by_id["N-003"]["lineage"]["bundle"] = ["SQ001-SC001-SH002",
                                               "SQ001-SC001-SH003"]
        with open(os.path.join(self.job_dir, "revision", "notes.json"), "w") as fh:
            json.dump(doc, fh, indent=2)

        state_path = os.path.join(self.job_dir, "state.json")
        state = json.load(open(state_path))
        state["gates"] = _gates()
        state["ledger"] = _ledger()
        state["budget"] = {"ceiling_usd": 25.0, "actual_usd": 2.79}
        state["escalations"] = [dict(ESCALATION)] if escalated else []
        state["position"] = {"step": 9, "name": "RESUBMIT",
                             "parked": bool(escalated)}
        with open(state_path, "w") as fh:
            json.dump(state, fh, indent=2)
        return doc

    def build(self, *args):
        cp = U.run_tool("build_round_report.py", self.job_dir, *args)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        return json.load(open(self.report))

    def check(self, path=None):
        return U.run_tool("build_round_report.py", "--check",
                          path or self.report)

    def test_escalated_terminal_state_validates(self):
        r = self.build()
        self.assertEqual(r["terminal_state"], "escalated")
        self.assertEqual(r["report_version"], "1.0.0")
        self.assertEqual(r["job_id"], self.job_id)
        self.assertEqual(len(r["escalations"]), 1)
        self.assertTrue(r["escalations"][0]["parked"])
        self.assertEqual(r["escalations"][0]["trigger"], "repair_exhaustion")
        cp = self.check()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        md = os.path.join(self.job_dir, "out", "round_report.md")
        self.assertTrue(os.path.exists(md))
        text = open(md).read()
        self.assertIn("ESCALATED", text)
        self.assertIn("N-001", text)
        self.assertIn("DELEGATED SELF-APPROVAL", text)

    def test_resubmitted_terminal_state_validates(self):
        self.seed(escalated=False)
        r = self.build()
        self.assertEqual(r["terminal_state"], "resubmitted")
        self.assertEqual(r["escalations"], [])
        self.assertEqual(r["totals"]["unresolved"], 0)
        cp = self.check()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("RESUBMITTED", open(
            os.path.join(self.job_dir, "out", "round_report.md")).read())

    def test_per_note_fields_present(self):
        r = self.build()
        by_id = {n["note_id"]: n for n in r["notes"]}
        self.assertEqual(set(by_id), {"N-001", "N-002", "N-003"})
        for n in r["notes"]:
            for k in ("classification", "method", "method_label", "status",
                      "cycles_used", "gates", "verification", "cost"):
                self.assertIn(k, n, f"{n['note_id']} missing {k}")
        n1 = by_id["N-001"]
        self.assertEqual(n1["classification"], "local")
        self.assertEqual(n1["method"], 3)
        self.assertEqual(n1["method_label"], "direct video edit")
        self.assertEqual(n1["cycles_used"], 2)
        self.assertEqual([v["verdict"] for v in n1["verification"]],
                         ["FAIL", "PASS"])
        self.assertIn("qc/round1/N-001/v2.json",
                      n1["verification"][1]["qc_refs"])
        self.assertAlmostEqual(n1["cost"]["est_usd"], 3.92, places=2)
        self.assertAlmostEqual(n1["cost"]["actual_usd"], 1.94, places=2)
        self.assertEqual(by_id["N-002"]["classification"], "foundational")
        n3 = by_id["N-003"]
        self.assertEqual(n3["escalations"], ["repair_exhaustion"])
        self.assertEqual(n3["bundle"], ["SQ001-SC001-SH002", "SQ001-SC001-SH003"])
        self.assertAlmostEqual(n3["cost"]["actual_usd"], 0.0, places=2)

    def test_delegated_gate_self_approval_is_auditable(self):
        r = self.build()
        self.assertEqual(len(r["delegated_gate_approvals"]), 1)
        d = r["delegated_gate_approvals"][0]
        self.assertEqual((d["gate"], d["decider"], d["outcome"]),
                         ("stills", "agent", "approved"))
        self.assertTrue(d["evidence"], "delegated approval needs evidence")
        by_id = {n["note_id"]: n for n in r["notes"]}
        self.assertEqual([g["decider"] for g in by_id["N-001"]["gates"]],
                         ["agent"])
        # gate-string attribution: "stills:N-003" lands on the note
        self.assertEqual([g["gate"] for g in by_id["N-003"]["gates"]],
                         ["stills:N-003"])
        self.assertEqual([g["gate"] for g in r["round_gates"]], ["lock"])
        self.assertEqual(r["gate_config"]["stills"], "agent")
        self.assertEqual(r["gate_config"]["lock"], "human")

    def test_totals_sum(self):
        r = self.build()
        t = r["totals"]
        self.assertEqual(t["notes"], 3)
        self.assertEqual(t["addressed"] + t["unresolved"], t["notes"])
        self.assertEqual((t["addressed"], t["unresolved"]), (2, 1))
        self.assertAlmostEqual(t["est_usd"],
                               sum(n["cost"]["est_usd"] for n in r["notes"]),
                               places=2)
        self.assertAlmostEqual(t["est_usd"], 9.32, places=2)
        self.assertAlmostEqual(t["actual_usd"], 2.79, places=2)
        self.assertAlmostEqual(
            sum(n["cost"]["actual_usd"] for n in r["notes"])
            + t["unattributed_actual_usd"], t["actual_usd"], places=2)
        self.assertEqual((t["tool_invocations"], t["failed_invocations"]), (5, 1))
        self.assertEqual(t["delegated_gate_approvals"], 1)
        self.assertAlmostEqual(r["budget"]["ceiling_usd"], 25.0, places=2)

    def test_check_catches_mutilated_report(self):
        r = self.build()
        del r["notes"][0]["method"]
        r["notes"][1]["classification"] = "sideways"
        r["totals"]["est_usd"] = 1.0
        r["delegated_gate_approvals"][0]["evidence"] = []
        bad = os.path.join(self.job_dir, "out", "bad.json")
        json.dump(r, open(bad, "w"), indent=2)
        cp = self.check(bad)
        self.assertNotEqual(cp.returncode, 0)
        err = U.stderr_error(cp)
        self.assertEqual(err["error"], "invalid_report")
        joined = " | ".join(err["errors"])
        self.assertIn("notes[0].method: missing", joined)
        self.assertIn("notes[1].classification", joined)
        self.assertIn("totals.est_usd", joined)
        self.assertIn("delegated_gate_approvals[0].evidence", joined)

    def test_check_rejects_terminal_state_contradiction(self):
        r = self.build()
        r["terminal_state"] = "resubmitted"      # but an escalation is parked
        bad = os.path.join(self.job_dir, "out", "bad2.json")
        json.dump(r, open(bad, "w"), indent=2)
        cp = self.check(bad)
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("parked", " ".join(U.stderr_error(cp)["errors"]))

    def test_missing_notes_json_is_a_clean_error(self):
        os.remove(os.path.join(self.job_dir, "revision", "notes.json"))
        cp = U.run_tool("build_round_report.py", self.job_dir)
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "no_notes_json")

if __name__ == "__main__":
    unittest.main()
