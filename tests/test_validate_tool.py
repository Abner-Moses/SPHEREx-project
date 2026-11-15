"""The stdlib-only validator must agree exactly with the server's footprint test."""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import spherex_pipeline as pipeline

spec = importlib.util.spec_from_file_location("validate_tool", Path(__file__).resolve().parents[1] / "tools" / "validate_archive_coverage.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def test_both_footprint_tests_agree_on_random_polygons_everywhere():
    rng = random.Random(11)
    for _ in range(400):
        ra, dec = rng.uniform(0, 360), rng.choice([rng.uniform(-90, 90), rng.uniform(80, 90), rng.uniform(-90, -80)])
        half = rng.uniform(0.5, 3.0)
        verts = [(ra - half, dec - half), (ra + half, dec - half), (ra + half, dec + half), (ra - half, dec + half)]
        verts = [(lon % 360, max(-89.9, min(89.9, lat))) for lon, lat in verts]
        qra, qdec = ra + rng.uniform(-4, 4), max(-90, min(90, dec + rng.uniform(-4, 4)))
        qra %= 360
        assert tool.contains_region(verts, qra, qdec) == pipeline.region_contains(verts, qra, qdec)


def test_both_parse_the_same_region_text():
    text = "POLYGON ICRS 268.4 3.9 271.9 3.6 271.8 7.1 268.3 7.4"
    assert tool.parse_region(text) == pipeline.parse_region(text)
