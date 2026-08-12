"""LIVE TIER — REAL API CALLS, REAL (small) SPEND.

Never runs in the default tier: run_tests.sh only includes it with --live.
One minimal call per paid API to prove credentials and contract:
  - Supabase storage: upload + public fetch + delete (storage only)
  - Ark images (Seedream 5 Pro): one small still edit (~cents)
  - WaveSpeed (Nano Banana Pro): one small still edit (~cents)
Seedance video and the quota probe are deliberately excluded — the probe
is its own --confirm-gated operation (see README).
"""
import json, os, sys, unittest
import _util as U

sys.path.insert(0, U.TOOLS)
import ark_client as ark   # noqa: E402

LIVE = os.environ.get("REVISION_AGENT_LIVE") == "1"


@unittest.skipUnless(LIVE, "live tier only (run_tests.sh --live)")
class TestLiveAPIs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        U.ensure_fixtures()
        ark.load_env()
        cls.tmp = os.path.join(U.GEN, "live")
        os.makedirs(cls.tmp, exist_ok=True)

    def test_supabase_upload_roundtrip(self):
        import requests
        import supabase_upload as su
        cache = os.path.join(self.tmp, "uploads.json")
        res = su.upload(U.still(), cache, prefix="tests", force=True)
        r = requests.get(res["url"], timeout=30)
        self.assertEqual(r.status_code, 200)
        # clean up the test object
        base = os.environ["SUPABASE_URL"].rstrip("/")
        bucket = os.environ["SUPABASE_STORAGE_BUCKET"]
        key = os.environ["SUPABASE_KEY"]
        d = requests.delete(f"{base}/storage/v1/object/{bucket}/{res['object']}",
                            headers={"apikey": key,
                                     "Authorization": f"Bearer {key}"},
                            timeout=30)
        self.assertLess(d.status_code, 300)

    def test_seedream_edit_contract(self):
        cp = U.run_tool("image_edit.py", "--still", U.still(),
                        "--prompt", "tint the whole image slightly red",
                        "--model", "seedream5pro", "--out-dir", self.tmp)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        out = json.loads(cp.stdout)["out"]
        self.assertTrue(os.path.exists(out))
        self.assertGreater(os.path.getsize(out), 1000)

    def test_nanobanana_edit_contract(self):
        cp = U.run_tool("image_edit.py", "--still", U.still(),
                        "--prompt", "tint the whole image slightly blue",
                        "--model", "nanobananapro", "--out-dir", self.tmp)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        out = json.loads(cp.stdout)["out"]
        self.assertTrue(os.path.exists(out))
        self.assertGreater(os.path.getsize(out), 1000)


if __name__ == "__main__":
    unittest.main()
