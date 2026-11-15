"""SphereX2's own tests, run inside the merged repo: its app module is science/sci_main.py (renamed from main.py so it
does not clash with the Sky Explorer's main.py), and its caches live under science/cache."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
os.environ.setdefault("SPHEREX_CACHE", str(HERE / "cache" / "observations"))
os.environ.setdefault("SPHEREX_HIPS_CACHE", str(HERE / "cache" / "hips"))
sys.path.insert(0, str(HERE))
import sci_main  # noqa: E402

sys.modules.setdefault("main", sci_main)
