"""Zero-install launcher: ``python packages/data-matching/run.py <command> ...``."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE / "src"), str(HERE.parents[1] / "connectors")]

from ct_matching.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
