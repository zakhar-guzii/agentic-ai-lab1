"""Chornohora trail dataset: format, loading, direction of travel along a segment. No MCP or network."""

import functools
import json
from pathlib import Path
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, Field, ValidationError, model_validator

DATASET_PATH = Path(__file__).resolve().parents[3] / "data" / "chornohora.json"
REBUILD_COMMAND = "uv run python scripts/build_dataset.py"

NodeKind = Literal["peak", "junction", "hut", "camp_site", "village"]
OVERNIGHT_KINDS = frozenset({"hut", "camp_site", "village"})


class DomainError(Exception):
    """The request cannot be fulfilled by the logic. `code` is one of the server error codes."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class DatasetError(Exception):
    """The dataset file is missing or is not a dataset. The message names the file and the rebuild command."""


# --- Dataset (format: design D3, spec trail-dataset) ---


class ForecastCity(BaseModel):
    name: str
    lat: float
    lon: float
    ele_m: int


class Node(BaseModel):
    id: str
    name: str | None
    names: list[str]
    kind: NodeKind
    lat: float
    lon: float
    ele_m: int
    forecast_city: str
    forecast_city_ele_m: int
    forecast_city_km: float


class Segment(BaseModel):
    id: str
    a: str
    b: str
    km: float = Field(gt=0)
    ascent_m: int = Field(ge=0)
    descent_m: int = Field(ge=0)
    max_ele_m: int
    above_treeline: bool
    exposed_ridge: bool
    synthetic: bool
    osm_way_ids: list[int]


class Dataset(BaseModel):
    dataset_version: str
    built_at: str
    bbox: Any
    dem: dict[str, Any]
    forecast_cities: list[ForecastCity]
    nodes: list[Node]
    segments: list[Segment]

    @model_validator(mode="after")
    def _check_references(self) -> "Dataset":
        node_ids = {n.id for n in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("node ids are not unique")
        if len({s.id for s in self.segments}) != len(self.segments):
            raise ValueError("segment ids are not unique")
        for s in self.segments:
            if s.a not in node_ids or s.b not in node_ids:
                raise ValueError(f"segment {s.id!r} references a missing node")
        return self

    @functools.cached_property
    def nodes_by_id(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}


def read_dataset(path: Path) -> Dataset:
    """Reads and validates the dataset file; raises `DatasetError` on any problem."""
    try:
        return Dataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        problem = "file not found"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        problem = f"cannot read it as JSON ({exc})"
    except ValidationError as exc:
        problem = f"not a valid trail dataset ({exc.error_count()} errors, first: {_first_error(exc)})"
    raise DatasetError(f"dataset {path}: {problem}. Rebuild it with: {REBUILD_COMMAND}")


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    return f"{'.'.join(map(str, err['loc'])) or '<root>'}: {err['msg']}"


@functools.cache
def load_dataset() -> Dataset:
    """The dataset from `DATASET_PATH`, loaded once per process."""
    return read_dataset(DATASET_PATH)


class Traversal(NamedTuple):
    to_node: str
    km: float
    ascent_m: int
    descent_m: int


def traverse(segment: Segment, from_node: str) -> Traversal:
    """Segment in the direction of travel from `from_node`: b→a swaps gain and loss."""
    if from_node == segment.a:
        return Traversal(segment.b, segment.km, segment.ascent_m, segment.descent_m)
    if from_node == segment.b:
        return Traversal(segment.a, segment.km, segment.descent_m, segment.ascent_m)
    raise DomainError("INVALID_INPUT", f"node {from_node!r} is not an end of segment {segment.id!r}")
