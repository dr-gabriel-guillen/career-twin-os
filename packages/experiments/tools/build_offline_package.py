"""Build one reproducible offline package of lane B.

    python packages/experiments/tools/build_offline_package.py [--out DIR]

Writes careertwin-lane-b-<version>.zip containing both packages, their tests,
the fixtures and the frozen contracts they validate against. The archive is
byte-for-byte reproducible (sorted entries, fixed timestamps, LF sources), so
its sha256 identifies the exact code. Nothing is uploaded or installed.

To use it: unzip anywhere, then `python packages/experiments/run_tests.py`.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INCLUDE = ("packages/applications", "packages/experiments", "contracts")
SKIP_DIRS = {"__pycache__", "dist", ".pytest_cache"}
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def members() -> list[Path]:
    out = []
    for top in INCLUDE:
        for path in sorted((ROOT / top).rglob("*")):
            if path.is_file() and not (set(path.relative_to(ROOT).parts) & SKIP_DIRS) and path.suffix != ".pyc":
                out.append(path)
    return out


def build(out_dir: Path) -> tuple[Path, str]:
    sys.path.insert(0, str(ROOT / "packages" / "experiments"))
    from ct_experiments import __version__
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"careertwin-lane-b-{__version__}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in members():
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes().replace(b"\r\n", b"\n"))
    return target, hashlib.sha256(target.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "dist"))
    target, digest = build(Path(parser.parse_args().out))
    print(f"{target}\nsha256 {digest}\nfiles {len(members())}")
