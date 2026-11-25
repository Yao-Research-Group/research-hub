#!/usr/bin/env python3
"""Backward-compatible wrapper delegating to advisor_collect.py (v3.1.0)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import advisor_collect  # noqa: E402

DEFAULT_HUB_REPO = advisor_collect.DEFAULT_HUB_REPO
DEFAULT_HUB_BRANCH = advisor_collect.DEFAULT_HUB_BRANCH


def main(argv: Optional[List[str]] = None) -> int:
    return advisor_collect.run_cli(argv, default_workspace="~/.yao-research-secretary")


if __name__ == "__main__":
    raise SystemExit(main())
