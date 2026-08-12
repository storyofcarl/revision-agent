import json, os, shutil, tempfile, unittest
import _util as U

class TestSupabaseUpload(unittest.TestCase):
    def setUp(self):
        U.ensure_fixtures()
        self.tmp = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmp, "uploads.json")
        self.f = os.path.join(self.tmp, "ref.txt")
        open(self.f, "w").write("reference payload")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *extra):
        return U.run_tool("supabase_upload.py", self.f, "--cache", self.cache,
                          "--mock", *extra)

    def test_upload_then_cache_hit(self):
        cp1 = self._run()
        self.assertEqual(cp1.returncode, 0, cp1.stderr)
        r1 = json.loads(cp1.stdout)
        self.assertFalse(r1["cached"])
        self.assertTrue(r1["url"].startswith("https://"))
        cp2 = self._run()
        r2 = json.loads(cp2.stdout)
        self.assertTrue(r2["cached"])
        self.assertEqual(r1["url"], r2["url"])

    def test_content_hash_keying_survives_rename(self):
        r1 = json.loads(self._run().stdout)
        renamed = os.path.join(self.tmp, "renamed.txt")
        shutil.copyfile(self.f, renamed)
        cp = U.run_tool("supabase_upload.py", renamed, "--cache", self.cache,
                        "--mock")
        r2 = json.loads(cp.stdout)
        self.assertTrue(r2["cached"])           # same content => same hash
        self.assertEqual(r1["sha256"], r2["sha256"])

    def test_force_bypasses_cache(self):
        self._run()
        cp = self._run("--force")
        self.assertFalse(json.loads(cp.stdout)["cached"])

    def test_oversized_file_refused_preflight(self):
        big = os.path.join(self.tmp, "big.bin")
        with open(big, "wb") as fh:               # sparse 101MB — instant
            fh.seek(101 * 1_000_000)
            fh.write(b"\0")
        cp = U.run_tool("supabase_upload.py", big, "--cache", self.cache,
                        "--mock")
        self.assertNotEqual(cp.returncode, 0)
        err = U.stderr_error(cp)
        self.assertEqual(err["error"], "over_cap")

    def test_retention_cleanup_drops_expired_entries(self):
        self._run()
        cache = json.load(open(self.cache))
        for k in cache:
            cache[k]["ts"] -= 8 * 86400            # age past retention
        json.dump(cache, open(self.cache, "w"))
        cp = self._run()                            # re-run triggers cleanup
        r = json.loads(cp.stdout)
        self.assertFalse(r["cached"])               # expired => re-uploaded
        cache = json.load(open(self.cache))
        self.assertEqual(len(cache), 1)             # only the fresh entry

    def test_missing_file_error(self):
        cp = U.run_tool("supabase_upload.py", "nope.bin", "--cache",
                        self.cache, "--mock")
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(U.stderr_error(cp)["error"], "no_such_file")

if __name__ == "__main__":
    unittest.main()
