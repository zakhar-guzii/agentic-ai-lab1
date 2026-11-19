"""Synthetic dataset for the domain-module and trails_server tests (independent of data/chornohora.json)."""

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from carpathian_planner.trails import dataset


def node(id, name, kind, ele_m, names=None, lat=48.15, lon=24.5):
    return {
        "id": id,
        "name": name,
        "names": names if names is not None else ([name] if name else []),
        "kind": kind,
        "lat": lat,
        "lon": lon,
        "ele_m": ele_m,
        "forecast_city": "Yasinia,UA",
        "forecast_city_ele_m": 650,
        "forecast_city_km": 8.0,
    }


NODES = [
    node("n1", "Говерла", "peak", 2061, names=["Говерла", "Hoverla"], lat=48.16, lon=24.5),
    node("n2", "Піп Іван Чорногірський", "peak", 2028, names=["Піп Іван Чорногірський", "Pip Ivan"]),
    node("n3", "Піп Іван", "peak", 1936),
    node("n4", "Петрос", "peak", 2020),
    node("n5", "Бребенескул", "peak", 2035),
    node("n6", "Туркул", "peak", 1933),
    node("n7", "Шпиці", "peak", 1863),
    node("n8", "Вершина 1850", "peak", 1850),
    node("n9", "Пожижевська", "peak", 1822),
    node("n10", "Гомул", "peak", 1788),
    node("n11", "Кукул", "peak", 1539),
    node("n12", "Дземброня", "peak", 1877),
    node("n20", "Заросляк", "hut", 1290),
    node("n21", "Ворохта", "village", 850),
    node("n22", "Стоянка Козьмещик", "camp_site", 1100),
    node("n23", None, "junction", 1600),
]

SEGMENTS = [
    {
        "id": "n1~n20",
        "a": "n1",
        "b": "n20",
        "km": 3.2,
        "ascent_m": 100,
        "descent_m": 871,
        "max_ele_m": 2061,
        "above_treeline": True,
        "exposed_ridge": True,
        "synthetic": False,
        "osm_way_ids": [1],
    }
]


def dataset_dict(nodes=NODES, segments=SEGMENTS):
    return {
        "dataset_version": "2026-11-16-0123abcd",
        "built_at": "2026-11-16",
        "bbox": [48.0, 24.3, 48.3, 24.7],
        "dem": {"provider": "opentopodata", "dataset": "srtm30m", "step_m": 100},
        "forecast_cities": [{"name": "Yasinia,UA", "lat": 48.27, "lon": 24.36, "ele_m": 650}],
        "nodes": nodes,
        "segments": segments,
    }


@pytest.fixture
def synthetic_dataset_path(tmp_path):
    path = tmp_path / "chornohora.json"
    path.write_text(json.dumps(dataset_dict(), ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def synthetic_dataset(synthetic_dataset_path, monkeypatch):
    """Swaps the server dataset for the synthetic one and clears the loader cache before and after the test."""
    monkeypatch.setattr(dataset, "DATASET_PATH", synthetic_dataset_path)
    dataset.load_dataset.cache_clear()
    yield dataset.load_dataset()
    dataset.load_dataset.cache_clear()


# --- carpathian-trails as a separate process ---

TRAILS_BIN = Path(sys.executable).parent / "carpathian-trails"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="session")
def server():
    """The real `carpathian-trails` with the committed dataset on a free port."""
    port = free_port()
    proc = subprocess.Popen(
        [str(TRAILS_BIN), "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    lines = []
    threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True).start()
    deadline = time.monotonic() + 30
    while not port_open(port):
        if proc.poll() is not None or time.monotonic() > deadline:
            proc.kill()
            time.sleep(0.2)
            pytest.fail(f"server did not start: rc={proc.poll()} output={''.join(lines)}")
        time.sleep(0.1)
    yield port, lines
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture
def ridge_server(tmp_path, monkeypatch):
    """trails server tools on the G-ridge graph (tests/planning_graphs.py); the plan store is empty and per-test."""
    from carpathian_planner.trails import server as trails_server
    from carpathian_planner.trails.plan_store import PlanStore
    from planning_graphs import g_ridge

    path = tmp_path / "chornohora.json"
    path.write_text(json.dumps(g_ridge().model_dump()), encoding="utf-8")
    monkeypatch.setattr(dataset, "DATASET_PATH", path)
    monkeypatch.setattr(trails_server, "plan_store", PlanStore())
    dataset.load_dataset.cache_clear()
    yield trails_server.plan_store
    dataset.load_dataset.cache_clear()
