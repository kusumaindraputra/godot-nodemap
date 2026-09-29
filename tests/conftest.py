import shutil
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "demo_game"


@pytest.fixture()
def game(tmp_path) -> Path:
    """A fresh, writable copy of the demo project."""
    dst = tmp_path / "demo_game"
    shutil.copytree(FIXTURE, dst, ignore=shutil.ignore_patterns("nodemap-out"))
    return dst


@pytest.fixture()
def graph(game):
    from godot_nodemap import analyze
    from godot_nodemap.build import build

    g = build(game, use_cache=False)
    analyze.cluster(g)
    return g
