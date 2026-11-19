"""Builders of stages, plans and forecast slots for the weather-rule and check_weather_warnings tests."""



CITY = "Yasinia,UA"
OTHER = "Vorokhta,UA"


def stage(day=1, date="2026-11-20", window=("05:35", "14:40"), city=CITY, city_ele=650, max_ele=650,
          above_treeline=False, exposed_ridge=False, city_km=8.0):
    """A plan stage with the fields the weather check reads (it needs no other fields)."""
    return {"day": day, "date": date, "max_ele_m": max_ele, "above_treeline": above_treeline,
            "exposed_ridge": exposed_ridge, "forecast_city": city, "forecast_city_ele_m": city_ele,
            "forecast_city_km": city_km,
            "check_window": {"from_utc": f"{date}T{window[0]}:00Z", "to_utc": f"{date}T{window[1]}:00Z"}}  # fmt: skip


def plan(*stages, plan_id="p_0123456789"):
    return {"plan_id": plan_id, "start_date": stages[0]["date"], "days": len(stages), "stages": list(stages)}


def slot(time_utc, condition="Clouds", temp_c=5.0, city=CITY):
    return {"city": city, "time_utc": time_utc, "condition": condition, "temp_c": temp_c}


def slots_at(date, hours, condition="Clouds", temp_c=5.0, city=CITY):
    return [slot(f"{date}T{h:02d}:00:00Z", condition, temp_c, city) for h in hours]


def iso(times):
    return [t.strftime("%H:%M") for t in times]
