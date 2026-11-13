"""Put both lane-B packages on sys.path for unittest and pytest alike."""
import sys
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2]
for name in ("applications", "experiments"):
    p = str(PACKAGES / name)
    if p not in sys.path:
        sys.path.insert(0, p)
