"""Walking time and day limit: DIN 33466 per segment, sunrise/sunset, min(norm, daylight − 1 h)."""

import datetime
import math
from fractions import Fraction
from typing import NamedTuple

FITNESS_FACTOR = {"low": Fraction(5, 4), "medium": Fraction(1), "high": Fraction(17, 20)}
FITNESS_NORM_MINUTES = {"low": 6 * 60, "medium": 8 * 60, "high": 10 * 60}


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
    """Sunrise and sunset (Sun centre at −0.833°) in UTC minutes from midnight of `day`; simplified NOAA formulas."""

    def event(rising: bool) -> float:
        t = 720.0  # first guess is UTC noon; two iterations refine the event time
        for _ in range(3):
            jd = day.toordinal() + 1721424.5 + t / 1440
            c = (jd - 2451545) / 36525
            l0 = (280.46646 + c * (36000.76983 + c * 0.0003032)) % 360
            m = 357.52911 + c * (35999.05029 - 0.0001537 * c)
            e = 0.016708634 - c * (0.000042037 + 0.0000001267 * c)
            mr = math.radians(m)
            center = (
                math.sin(mr) * (1.914602 - c * (0.004817 + 0.000014 * c))
                + math.sin(2 * mr) * (0.019993 - 0.000101 * c)
                + math.sin(3 * mr) * 0.000289
            )
            omega = math.radians(125.04 - 1934.136 * c)
            app_long = math.radians(l0 + center - 0.00569 - 0.00478 * math.sin(omega))
            eps0 = 23 + (26 + (21.448 - c * (46.815 + c * (0.00059 - c * 0.001813))) / 60) / 60
            eps = math.radians(eps0 + 0.00256 * math.cos(omega))
            decl = math.asin(math.sin(eps) * math.sin(app_long))
            y = math.tan(eps / 2) ** 2
            l0r = math.radians(l0)
            eq_time = 4 * math.degrees(
                y * math.sin(2 * l0r)
                - 2 * e * math.sin(mr)
                + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                - 0.5 * y * y * math.sin(4 * l0r)
                - 1.25 * e * e * math.sin(2 * mr)
            )
            phi = math.radians(lat)
            ha = math.degrees(
                math.acos(
                    math.cos(math.radians(90.833)) / (math.cos(phi) * math.cos(decl))
                    - math.tan(phi) * math.tan(decl)
                )
            )
            t = 720 - 4 * (lon + (ha if rising else -ha)) - eq_time
        return t

    return event(True), event(False)


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
    daylight = math.floor(set_ - rise)
    norm = FITNESS_NORM_MINUTES[fitness]
    window = {"from_utc": _utc(day, math.floor(rise)), "to_utc": _utc(day, math.ceil(set_))}
    if norm <= daylight - 60:
        return DayLimit(norm, "fitness", window)
    return DayLimit(daylight - 60, "daylight", window)
