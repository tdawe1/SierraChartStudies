"""paper_orb failure modes: missing converter, env overrides, skip-convert."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paper_orb import SYSTEMS, _scid_path, main


def _bars_paths():
    return [f"/home/user/bt-data/paper-{name}.csv" for name in SYSTEMS]


class PaperOrbTests(unittest.TestCase):
    def test_missing_scid_without_skip_convert_is_clean_error(self):
        try:
            __import__("scid")
        except ImportError:
            pass
        else:
            self.skipTest("scid converter installed; nothing to fall back from")
        self.assertEqual(main(["--run"]), 2)

    def test_scid_path_env_override(self):
        os.environ["PAPER_ORB_SCID_NQ_ALL"] = "/tmp/NQZ26-CME.scid"
        try:
            self.assertEqual(_scid_path("nq-all", "default"),
                             "/tmp/NQZ26-CME.scid")
            self.assertEqual(_scid_path("ym-val", "default"), "default")
        finally:
            del os.environ["PAPER_ORB_SCID_NQ_ALL"]

    def test_skip_convert_missing_csvs_skips_all(self):
        if any(os.path.exists(p) for p in _bars_paths()):
            self.skipTest("per-system CSVs present; would write ledgers")
        self.assertEqual(main(["--run", "--skip-convert"]), 0)


if __name__ == "__main__":
    unittest.main()
