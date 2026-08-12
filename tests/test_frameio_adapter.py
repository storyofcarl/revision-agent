import sys, unittest
import _util as U

sys.path.insert(0, U.TOOLS)
import frameio_adapter as fio   # noqa: E402


class TestFrameioAdapter(unittest.TestCase):
    def test_stub_raises_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            fio.fetch_comments("asset-123")

    def test_rawcomment_shape_matches_intake_json_contract(self):
        rc = fio.RawComment(comment_id="c1", asset_id="a1", author="Dana",
                            text="jacket red", timecode_in="00:00:05:00",
                            fps_base=24)
        d = rc.__dict__
        # exactly the keys intake_normalize.parse_frameio consumes
        for k in ("comment_id", "asset_id", "author", "text", "timecode_in",
                  "timecode_out", "fps_base", "parent_id", "created_at"):
            self.assertIn(k, d)

if __name__ == "__main__":
    unittest.main()
