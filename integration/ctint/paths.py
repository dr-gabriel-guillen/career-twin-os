"""Repository path discovery. ``CT_REPO_ROOT`` lets the runner point at a worktree."""

from __future__ import annotations

import os
from pathlib import Path


def integration_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def repo_root() -> Path:
    env = os.environ.get("CT_REPO_ROOT")
    return Path(env).resolve() if env else integration_dir().parent


def contracts_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "contracts"


def fixtures_dir() -> Path:
    return integration_dir() / "fixtures"
