"""Deterministic everyday-intelligence operations and bounded public data clients."""

from __future__ import annotations

import ast
import operator
import re
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

_NOMINATIM_LOCK = threading.Lock()
_NOMINATIM_LAST_CALL = 0.0


class IntelligenceError(ValueError):
    pass


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise IntelligenceError("Unknown IANA timezone name") from None


def date_time_operation(
    operation: str,
    timezone_name: str = "America/Halifax",
    source_timezone: str | None = None,
    timestamp: str | None = None,
    other_timestamp: str | None = None,
    days: int = 0,
    relative: str | None = None,
    now: datetime | None = None,
):
    zone = _zone(timezone_name)
    current = (now or datetime.now(zone)).astimezone(zone)
    if operation == "current":
        value = current
    elif operation == "convert":
        if not timestamp or not source_timezone:
            raise IntelligenceError("Conversion requires timestamp and source_timezone")
        parsed = datetime.fromisoformat(timestamp)
        value = (
            parsed.replace(tzinfo=_zone(source_timezone)) if parsed.tzinfo is None
            else parsed.astimezone(_zone(source_timezone))
        ).astimezone(zone)
    elif operation == "add_days":
        value = (datetime.fromisoformat(timestamp).astimezone(zone) if timestamp else current) + timedelta(days=days)
    elif operation == "difference":
        if not timestamp or not other_timestamp:
            raise IntelligenceError("Difference requires two ISO timestamps")
        first, second = datetime.fromisoformat(timestamp), datetime.fromisoformat(other_timestamp)
        seconds = Decimal(str((second - first).total_seconds()))
        return {"operation": operation, "seconds": str(seconds), "days": str(seconds / Decimal(86400))}
    elif operation == "relative":
        term = (relative or "").strip().casefold()
        if term == "tomorrow":
            value = current + timedelta(days=1)
        elif term == "yesterday":
            value = current - timedelta(days=1)
        elif term.startswith("next "):
            names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
            target = term[5:]
            if target not in names:
                raise IntelligenceError("Relative weekday must be a full weekday name")
            delta = (names.index(target) - current.weekday()) % 7 or 7
            value = current + timedelta(days=delta)
        else:
            raise IntelligenceError("Supported relative dates are tomorrow, yesterday and next WEEKDAY")
    else:
        raise IntelligenceError("Unsupported date/time operation")
    return {
        "operation": operation,
        "timezone": timezone_name,
        "timestamp": value.isoformat(),
        "date": value.date().isoformat(),
        "weekday": value.strftime("%A"),
        "utc_offset": value.strftime("%z"),
        "is_dst": bool(value.dst()),
    }


_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def calculate(expression: str):
    """Evaluate a bounded arithmetic expression without names, calls or eval()."""
    if len(expression) > 200:
        raise IntelligenceError("Expression is too long")
    expression = re.sub(r"(?<=\d)%(?=\s*(?:$|[+\-*/()]))", "/100", expression)

    def visit(node, depth=0):
        if depth > 12:
            raise IntelligenceError("Expression is too complex")
        if isinstance(node, ast.Expression):
            return visit(node.body, depth + 1)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return Decimal(str(node.value))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](visit(node.operand, depth + 1))
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
            if type(node.op) is ast.Pow and (abs(right) > 12 or abs(left) > Decimal("1e12")):
                raise IntelligenceError("Exponent is outside the safe range")
            try:
                return _BINARY[type(node.op)](left, right)
            except (InvalidOperation, ZeroDivisionError, OverflowError):
                raise IntelligenceError("Arithmetic operation is undefined") from None
        raise IntelligenceError("Only numeric arithmetic operators are allowed")

    try:
        value = visit(ast.parse(expression, mode="eval"))
    except (SyntaxError, ValueError):
        raise IntelligenceError("Invalid arithmetic expression") from None
    return {"expression": expression, "value": format(value.normalize(), "f"), "exact_decimal": True}


_UNITS = {
    ("km", "mi"): Decimal("0.621371192237334"),
    ("mi", "km"): Decimal("1.609344"),
    ("m", "ft"): Decimal("3.28083989501312"),
    ("ft", "m"): Decimal("0.3048"),
    ("kg", "lb"): Decimal("2.20462262184878"),
    ("lb", "kg"): Decimal("0.45359237"),
    ("l", "gal_us"): Decimal("0.264172052358148"),
    ("gal_us", "l"): Decimal("3.785411784"),
}


def convert_unit(value: float, from_unit: str, to_unit: str):
    source, target, amount = from_unit.casefold(), to_unit.casefold(), Decimal(str(value))
    if source == target:
        result = amount
    elif (source, target) == ("c", "f"):
        result = amount * Decimal(9) / Decimal(5) + Decimal(32)
    elif (source, target) == ("f", "c"):
        result = (amount - Decimal(32)) * Decimal(5) / Decimal(9)
    elif (source, target) in _UNITS:
        result = amount * _UNITS[(source, target)]
    else:
        raise IntelligenceError("Unsupported unit conversion")
    return {"value": str(amount), "from_unit": source, "to_unit": target, "result": str(result.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))}


def _get_json(url: str, params: dict, session=None):
    client = session or requests.Session()
    response = client.get(url, params=params, timeout=(5, 15), headers={"User-Agent": "DeskMate/1.0"})
    response.raise_for_status()
    return response.json()


def geocode_location(query: str, count: int = 5, session=None):
    if not query.strip() or not 1 <= count <= 10:
        raise IntelligenceError("Location query and count from 1 to 10 are required")
    data = _get_json("https://geocoding-api.open-meteo.com/v1/search", {"name": query, "count": count, "language": "en", "format": "json"}, session)
    return [{
        "name": row.get("name"), "country": row.get("country"), "admin1": row.get("admin1"),
        "latitude": row.get("latitude"), "longitude": row.get("longitude"),
        "timezone": row.get("timezone"), "source_url": "https://open-meteo.com/en/docs/geocoding-api",
    } for row in data.get("results", [])]


def search_places(query: str, near: str | None = None, count: int = 5, session=None):
    if not query.strip() or not 1 <= count <= 10:
        raise IntelligenceError("Place query and count from 1 to 10 are required")
    search = f"{query} near {near}" if near else query
    global _NOMINATIM_LAST_CALL
    with _NOMINATIM_LOCK:
        remaining = 1.0 - (time.monotonic() - _NOMINATIM_LAST_CALL)
        if remaining > 0 and session is None:
            time.sleep(remaining)
        data = _get_json(
            "https://nominatim.openstreetmap.org/search",
            {"q": search, "format": "jsonv2", "limit": count, "addressdetails": 1},
            session,
        )
        _NOMINATIM_LAST_CALL = time.monotonic()
    return [{
        "name": row.get("name") or row.get("display_name", "").split(",")[0],
        "display_name": row.get("display_name"), "category": row.get("category"),
        "type": row.get("type"), "latitude": row.get("lat"), "longitude": row.get("lon"),
        "source_id": f'openstreetmap:{row.get("osm_type")}:{row.get("osm_id")}',
        "source_url": "https://nominatim.org/release-docs/latest/api/Search/",
        "ratings_available": False, "hours_available": False,
    } for row in data]


def get_weather(location: str, forecast_days: int = 3, units: str = "metric", session=None):
    if not 1 <= forecast_days <= 14 or units not in {"metric", "imperial"}:
        raise IntelligenceError("forecast_days must be 1..14 and units metric or imperial")
    places = geocode_location(location, 1, session)
    if not places:
        raise IntelligenceError("Location was not found")
    place = places[0]
    params = {
        "latitude": place["latitude"], "longitude": place["longitude"],
        "timezone": place["timezone"] or "auto", "forecast_days": forecast_days,
        "current": "temperature_2m,apparent_temperature,precipitation,rain,snowfall,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,rain_sum,snowfall_sum,precipitation_probability_max,wind_speed_10m_max",
    }
    if units == "imperial":
        params.update(temperature_unit="fahrenheit", wind_speed_unit="mph", precipitation_unit="inch")
    data = _get_json("https://api.open-meteo.com/v1/forecast", params, session)
    daily = data.get("daily", {})
    keys = [key for key in daily if key != "time"]
    forecasts = [{"date": day, **{key: daily[key][index] for key in keys}} for index, day in enumerate(daily.get("time", []))]
    return {
        "provider": "Open-Meteo", "source_url": "https://open-meteo.com/en/docs",
        "location": place, "timezone": data.get("timezone") or place["timezone"],
        "observed_at": data.get("current", {}).get("time"), "current": data.get("current", {}),
        "current_units": data.get("current_units", {}), "forecast": forecasts,
        "forecast_units": data.get("daily_units", {}), "forecast_generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "alerts": None,
    }


def get_route(origin: str, destination: str, mode: str = "driving", session=None):
    if mode != "driving":
        raise IntelligenceError("The configured OSRM service supports driving routes only")
    start, end = geocode_location(origin, 1, session), geocode_location(destination, 1, session)
    if not start or not end:
        raise IntelligenceError("Origin or destination was not found")
    profile = "driving"
    coordinates = f'{start[0]["longitude"]},{start[0]["latitude"]};{end[0]["longitude"]},{end[0]["latitude"]}'
    data = _get_json(f"https://router.project-osrm.org/route/v1/{profile}/{coordinates}", {"overview": "false", "steps": "false"}, session)
    if data.get("code") != "Ok" or not data.get("routes"):
        raise IntelligenceError("No route was available")
    route = data["routes"][0]
    return {
        "provider": "OSRM", "source_url": "https://project-osrm.org/docs/v5.24.0/api/",
        "origin": start[0], "destination": end[0], "mode": mode,
        "distance_km": round(route["distance"] / 1000, 3),
        "duration_minutes": round(route["duration"] / 60, 1),
        "traffic_included": False,
    }


def convert_currency(amount: float, base: str, quote: str, rate_date: str = "latest", session=None):
    base, quote = base.upper(), quote.upper()
    Decimal(str(amount))
    data = _get_json(f"https://api.frankfurter.app/{rate_date}", {"amount": amount, "from": base, "to": quote}, session)
    if quote not in data.get("rates", {}):
        raise IntelligenceError("Exchange rate is unavailable")
    return {
        "provider": "Frankfurter/European Central Bank reference rates",
        "source_url": "https://frankfurter.dev/", "rate_date": data.get("date"),
        "base": base, "quote": quote, "amount": str(amount),
        "converted_amount": str(data["rates"][quote]),
    }


def get_holidays(country_code: str, year: int, subdivision: str | None = None, session=None):
    if not 1900 <= year <= 2200 or len(country_code) != 2:
        raise IntelligenceError("Use an ISO alpha-2 country and year from 1900 to 2200")
    data = _get_json(f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country_code.upper()}", {}, session)
    rows = []
    for row in data:
        counties = row.get("counties")
        if subdivision and counties and subdivision.upper() not in counties:
            continue
        rows.append({
            "date": row.get("date"), "name": row.get("name"), "local_name": row.get("localName"),
            "country_code": row.get("countryCode"), "subdivisions": counties,
            "national": not counties, "types": row.get("types", []),
            "source_url": "https://date.nager.at/",
        })
    return rows
