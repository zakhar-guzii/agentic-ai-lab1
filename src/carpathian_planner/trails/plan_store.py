"""Plan store of the trails server (design D10): recent plans in process memory, never written to disk."""

import threading
from collections import OrderedDict
from typing import Any

from carpathian_planner.trails.dataset import DomainError


class PlanStore:
    """The last `capacity` saved plans in process memory; never written to disk."""

    def __init__(self, capacity: int = 100) -> None:
        self.capacity = capacity
        self._plans: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, plan_id: str, plan: Any) -> None:
        """Saves a plan; saving it again makes it the newest, beyond `capacity` the oldest is evicted."""
        with self._lock:
            self._plans[plan_id] = plan
            self._plans.move_to_end(plan_id)
            while len(self._plans) > self.capacity:
                self._plans.popitem(last=False)

    def get(self, plan_id: str) -> Any:
        """Returns the plan or raises `DomainError("UNKNOWN_PLAN")`; reading does not change the order."""
        with self._lock:
            if plan_id not in self._plans:
                raise DomainError(
                    "UNKNOWN_PLAN",
                    f"plan {plan_id!r} is not stored; the server keeps only the last "
                    f"{self.capacity} plans in memory and forgets them on restart",
                )
            return self._plans[plan_id]

    def __len__(self) -> int:
        return len(self._plans)


plan_store = PlanStore()
