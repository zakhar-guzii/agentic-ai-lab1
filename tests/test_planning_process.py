"""plan_hike and validate_hike_plan on the committed dataset through a running `carpathian-trails` (tasks 6.1, 6.2).

One request per duration of 1-5 days (the same as in task 4.5 and in Open Questions of design.md, change
hike-planning); each plan must pass the validator with the same plan_id, and each plan_hike call must finish within 5 s.
"""

import asyncio
import time

import pytest
from fastmcp import Client

ZAROSLIAK = "w144275342"  # Zarosliak shelter (hut)
LAZESHCHYNA = "n337604448"  # Lazeshchyna village
YASINIA = "n337604455"  # Yasinia village
TROSTIANETS = "n337657878"  # Trostianets village
HOVERLA_VILLAGE = "r1466285"  # Hoverla village (the dataset has two village nodes with this name)
HOVERLA_VILLAGE_2 = "n337660353"  # the second Hoverla village
HOVERLA = "n703286388"  # Hoverla peak
PETROS = "n918903014"  # Petros peak, 2007 m (there is another peak named "Petros")
START_DATE = "2027-07-10"

REQUESTS = [
    dict(start_node_id=ZAROSLIAK, must_visit=[HOVERLA], days=1, overnight_mode="hut"),
    dict(start_node_id=LAZESHCHYNA, end_node_id=ZAROSLIAK, must_visit=[HOVERLA], days=2, overnight_mode="hut"),
    dict(start_node_id=LAZESHCHYNA, end_node_id=HOVERLA_VILLAGE, must_visit=[HOVERLA], days=3, overnight_mode="hut"),
    dict(start_node_id=YASINIA, end_node_id=HOVERLA_VILLAGE_2, must_visit=[PETROS, HOVERLA], days=4, overnight_mode="tent"),
    dict(
        start_node_id=TROSTIANETS,
        end_node_id=HOVERLA_VILLAGE_2,
        must_visit=[PETROS, HOVERLA],
        days=5,
        overnight_mode="tent",
    ),
]


@pytest.mark.parametrize("request_args", REQUESTS, ids=lambda r: f"{r['days']}d")
def test_plan_validates_with_same_plan_id_within_5s(server, request_args):
    port, _ = server
    common = dict(start_date=START_DATE, fitness="medium", overnight_mode=request_args["overnight_mode"])

    async def go():
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            t = time.perf_counter()
            planned = await client.call_tool("plan_hike", {**request_args, **common})
            elapsed = time.perf_counter() - t
            out = planned.structured_content
            checked = await client.call_tool(
                "validate_hike_plan", {"day_waypoints": out["day_waypoints"], **common}
            )
            return elapsed, out, checked.structured_content

    elapsed, out, checked = asyncio.run(go())
    assert out["status"] == "ok"
    assert len(out["plan"]["stages"]) == request_args["days"]
    assert checked["valid"] and checked["violations"] == []
    assert checked["plan"]["plan_id"] == out["plan"]["plan_id"]
    assert elapsed <= 5.0
