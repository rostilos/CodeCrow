#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codecrow_evaluation.cli import main  # noqa: E402


raise SystemExit(main())
