"""Walking time and day limit: DIN 33466 per segment, sunrise/sunset, min(norm, daylight − 1 h)."""

import datetime
import math
from fractions import Fraction
from typing import NamedTuple

from astral import Observer
from astral.sun import sun

FITNESS_FACTOR = {"low": Fraction(5, 4), "medium": Fraction(1), "high": Fraction(17, 20)}
FITNESS_NORM_MINUTES = {"low": 6 * 60, "medium": 8 * 60, "high": 10 * 60}
DAYLIGHT_RESERVE_MIN = 60  # the day ends an hour before sunset


def segment_minutes(km: float, ascent_m: int, descent_m: int, fitness: str) -> int:
    """Segment time in the direction of travel by DIN 33466 (as given by DAV), whole minutes, half rounds up (D2)."""
    h = Fraction(str(km)) / 4
    v = Fraction(ascent_m, 300) + Fraction(descent_m, 500)
    t = max(h, v) + min(h, v) / 2
    return math.floor(t * FITNESS_FACTOR[fitness] * 60 + Fraction(1, 2))


def to_hours(minutes: int) -> float:
    """Minutes → hours with two decimals. Exact halves never occur: 100·m/60 has a fractional part of 0, 1/3 or 2/3."""
    return round(minutes / 60, 2)


def km_to_m(km: float) -> int:
    return round(Fraction(str(km)) * 1000)


def sun_times(day: datetime.date, lat: float, lon: float) -> tuple[float, float]:
    """Sunrise and sunset (Sun centre at −0.833°) in UTC minutes from midnight of `day`, via astral."""
    s = sun(Observer(lat, lon), date=day)
    midnight = datetime.datetime.combine(day, datetime.time(), tzinfo=datetime.timezone.utc)

    def minutes(moment: datetime.datetime) -> float:
        return (moment - midnight).total_seconds() / 60

    return minutes(s["sunrise"]), minutes(s["sunset"])


class DayLimit(NamedTuple):
    minutes: int
    kind: str  # "fitness" | "daylight"
    check_window: dict[str, str]


def _utc(day: datetime.date, minutes: int) -> str:
    moment = datetime.datetime.combine(day, datetime.time()) + datetime.timedelta(minutes=minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:00Z")


def day_limit(day: datetime.date, lat: float, lon: float, fitness: str) -> DayLimit:
    """Day limit (D4): min(norm, daylight − 60 min); daylight rounds down, the window rounds outward to the minute."""
    rise, set_ = sun_times(day, lat, lon)
    usable_daylight = math.floor(set_ - rise) - DAYLIGHT_RESERVE_MIN
    norm = FITNESS_NORM_MINUTES[fitness]
    window = {"from_utc": _utc(day, math.floor(rise)), "to_utc": _utc(day, math.ceil(set_))}
    kind = "fitness" if norm <= usable_daylight else "daylight"
    return DayLimit(min(norm, usable_daylight), kind, window)
