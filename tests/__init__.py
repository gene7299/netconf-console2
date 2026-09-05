"""Test package; make the historical ncc/ source importable from the root."""

import sys
from pathlib import Path


source_root = Path(__file__).resolve().parents[1] / "ncc"
if str(source_root) not in sys.path:
    sys.path.insert(0, str(source_root))
