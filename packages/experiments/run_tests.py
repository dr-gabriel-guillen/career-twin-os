"""Run every lane B test, offline, with warnings as errors.

    python packages/experiments/run_tests.py

Stdlib only (unittest). Exit code 0 only if everything passed.
"""
from __future__ import annotations

import sys
import unittest
import warnings
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[1]


def main() -> int:
    warnings.simplefilter("error")
    suite = unittest.TestSuite()
    for tests in (PACKAGES / "applications" / "tests", PACKAGES / "experiments" / "tests"):
        sys.path.insert(0, str(tests))
        suite.addTests(unittest.defaultTestLoader.discover(str(tests), pattern="test_*.py", top_level_dir=str(tests)))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    print(f"LANE-B TESTS: ran={result.testsRun} failures={len(result.failures)} errors={len(result.errors)} "
          f"skipped={len(result.skipped)}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
