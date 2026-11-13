"""Regenerate analytics/reports/ from the synthetic generator. Offline; no arguments needed.

    python analytics/run_report.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "atlas"))

from careertwin_atlas.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["demo", "--out", str(ROOT / "analytics" / "reports")] + sys.argv[1:]))
