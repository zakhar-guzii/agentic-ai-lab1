"""find_place: search dataset nodes by name, type and elevation (design D9, spec place-search)."""

from typing import Any

from carpathian_planner.trails.dataset import OVERNIGHT_KINDS, DomainError, Node

NAME_RULES = (
    "Name matching is a case-insensitive substring of OSM names in the nominative case "
    "(e.g. 'Говерла', not 'Говерлу'), in Ukrainian, or in Latin only where OSM has a Latin name; "
    "there is no transliteration."
)


def _name_score(query: str, names: list[str]) -> float | None:
    """Best match among names: 1.0 exact, 0.8 prefix, 0.5 substring; None if there is no match."""
    return max(
        (1.0 if n == query else 0.8 if n.startswith(query) else 0.5 for n in map(str.casefold, names) if query in n),
        default=None,
    )


def find_place(
    nodes: list[Node],
    *,
    name: str | None = None,
    kind: str | None = None,
    min_ele_m: int | None = None,
    max_ele_m: int | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Returns `{"candidates": [...], "reason": str | None}`; filters are combined with AND."""
    if name is None and kind is None and min_ele_m is None and max_ele_m is None:
        raise DomainError(
            "INVALID_INPUT", "at least one filter is required: name, kind, min_ele_m or max_ele_m"
        )
    if min_ele_m is not None and max_ele_m is not None and min_ele_m > max_ele_m:
        raise DomainError(
            "INVALID_INPUT", f"min_ele_m ({min_ele_m}) must not exceed max_ele_m ({max_ele_m})"
        )
    query = name.strip().casefold() if name is not None else None
    if query == "":
        raise DomainError("INVALID_INPUT", "name: must not be blank")

    found = []
    for node in nodes:
        if kind is not None and node.kind != kind:
            continue
        if min_ele_m is not None and node.ele_m < min_ele_m:
            continue
        if max_ele_m is not None and node.ele_m > max_ele_m:
            continue
        score = 1.0 if query is None else _name_score(query, node.names)
        if score is not None:
            found.append((score, node))

    found.sort(key=lambda sn: (-sn[0], -sn[1].ele_m, sn[1].id))
    candidates = [_candidate(node, score) for score, node in found[:limit]]
    reason = None if candidates else _no_match_reason(name, kind, min_ele_m, max_ele_m)
    return {"candidates": candidates, "reason": reason}


def _candidate(node: Node, score: float) -> dict[str, Any]:
    return {
        "node_id": node.id,
        "name": node.name,
        "kind": node.kind,
        "lat": node.lat,
        "lon": node.lon,
        "ele_m": node.ele_m,
        "can_overnight": node.kind in OVERNIGHT_KINDS,
        "match_score": score,
    }


def _no_match_reason(name: str | None, kind: str | None, min_ele_m: int | None, max_ele_m: int | None) -> str:
    """Which filters were applied and the name-matching rules, so the model can fix its query."""
    filters = [
        f"{label}={value!r}" if label == "name" else f"{label}={value}"
        for label, value in (
            ("name", name.strip() if name is not None else None),
            ("kind", kind),
            ("min_ele_m", min_ele_m),
            ("max_ele_m", max_ele_m),
        )
        if value is not None
    ]
    return f"No place in the Chornohora dataset matches all of: {', '.join(filters)}. {NAME_RULES}"
