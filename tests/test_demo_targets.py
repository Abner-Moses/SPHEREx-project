"""Five-target bridge: aliases, epoch matching, routes. Runs without network (SPHEREX_OFFLINE=1)."""
import os
import sys
from pathlib import Path

import pytest

os.environ["SPHEREX_OFFLINE"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("astropy")
import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import demo_targets as d  # noqa: E402


def test_aliases_resolve_to_the_five_targets():
    assert d.resolve_alias("Barnard's Star") == "barnards-star"
    assert d.resolve_alias("eps ind b") == "eps-indi-b"
    assert d.resolve_alias("WISE 0855−0714") == "wise-0855"
    assert d.resolve_alias("rw aur") == "rw-aurigae"
    assert d.resolve_alias("R Dra") == "r-draconis"
    assert d.resolve_alias("Sirius") is None
    assert len(d.TARGETS) == 5


def test_fast_movers_match_at_survey_epoch_and_j2000():
    with TestClient(main.app) as c:
        # Barnard's Star has moved ~4.4 arcmin north since J2000: both positions must match a small frame
        assert c.get("/api/demo/match", params={"ra": 269.452, "dec": 4.693, "radius": 0.02}).json()["targets"][0]["id"] == "barnards-star"
        ra, dec = d._propagate(d.TARGETS["barnards-star"], d.EPOCH_YEAR)
        assert c.get("/api/demo/match", params={"ra": ra, "dec": dec, "radius": 0.02}).json()["targets"][0]["id"] == "barnards-star"
        assert c.get("/api/demo/match", params={"ra": 10, "dec": 10, "radius": 1}).json()["targets"] == []


def test_routes_and_errors():
    with TestClient(main.app) as c:
        body = c.get("/api/demo/targets").json()
        assert [t["id"] for t in body["targets"]] == list(d.TARGETS)
        assert body["offline"] is True
        assert c.get("/api/demo/targets/sirius/analysis").status_code == 404
        assert c.get("/api/demo/match", params={"ra": 400, "dec": 0, "radius": 1}).status_code == 422
        assert c.get("/api/science/status").json()["available"] is True
