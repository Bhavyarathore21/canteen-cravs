"""Lets the tests import the pipeline and backend modules."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for folder in ("pipeline", "backend"):
    sys.path.insert(0, str(ROOT / folder))
