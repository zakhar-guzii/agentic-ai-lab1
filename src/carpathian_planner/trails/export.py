"""export_plan: a stored plan as a GeoJSON FeatureCollection for maps (spec plan-export, design D1)."""

from typing import Any

from carpathian_planner.trails.dataset import Node
from carpathian_planner.trails.plan_rules import near_name, node_label

DAY_PROPERTIES = (
    "day", "date", "km", "ascent_m", "descent_m", "est_hours", "start_node_id", "end_node_id", "start_label",
    "end_label", "above_treeline", "exposed_ridge",
)  # fmt: skip


def _position(n: Node) -> list[float]:
    return [n.lon, n.lat, n.ele_m]  # GeoJSON order: longitude, latitude, elevation


def _point(role: str, n: Node, nodes: dict[str, Node], day: int | None = None) -> dict[str, Any]:
    props = {
        "role": role,
        "node_id": n.id,
        "label": node_label(n, nodes),
        "name": n.name,
        "near_name": None if n.name else near_name(n, nodes),
        "kind": n.kind,
        "ele_m": n.ele_m,
        "day": day,
    }
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": _position(n)}, "properties": props}


def export_plan(plan: dict[str, Any], nodes: dict[str, Node]) -> dict[str, Any]:
    """Start point, then per day its line and (except the last day) the overnight point, then the finish point."""
    stages = plan["stages"]
    features = [_point("start", nodes[stages[0]["start_node_id"]], nodes)]
    for s in stages:
        line = {"type": "LineString", "coordinates": [_position(nodes[i]) for i in s["path_node_ids"]]}
        features.append({"type": "Feature", "geometry": line, "properties": {"role": "day", **{k: s[k] for k in DAY_PROPERTIES}}})
        if s["overnight_node_id"]:
            features.append(_point("overnight", nodes[s["overnight_node_id"]], nodes, s["day"]))
    features.append(_point("finish", nodes[stages[-1]["end_node_id"]], nodes))
    return {"type": "FeatureCollection", "plan_id": plan["plan_id"], "plan": plan, "features": features}
