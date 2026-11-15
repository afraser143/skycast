"""Daily forecast and place lookup from Open-Meteo (free, no API key)."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import date

import httpx

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
TIMEOUT = httpx.Timeout(8.0)

# WMO weather interpretation codes, as used by Open-Meteo.
WMO = {
    0: "clear skies", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "rain showers", 82: "heavy rain showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}  # fmt: skip
WMO_EMOJI = {
    0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️", 45: "🌫️", 48: "🌫️", 95: "⛈️", 96: "⛈️", 99: "⛈️",
}  # fmt: skip


def describe_code(code: int) -> str:
    return WMO.get(code, "mixed weather")


def emoji_for(code: int) -> str:
    if code in WMO_EMOJI:
        return WMO_EMOJI[code]
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "🌧️"
    if 71 <= code <= 77 or code in (85, 86):
        return "🌨️"
    return "🌡️"


@dataclass
class Forecast:
    day: str
    summary: str
    emoji: str
    high: float
    low: float
    unit: str  # "°F" or "°C"
    rain_chance: int | None
    wind_max: float | None
    wind_unit: str
    uv_max: float | None
    sunrise: str | None  # "07:02"
    sunset: str | None

    def as_dict(self) -> dict:
        return asdict(self)

    def for_prompt(self) -> str:
        parts = [f"{self.summary}, high {self.high:.0f}{self.unit} / low {self.low:.0f}{self.unit}"]
        if self.rain_chance is not None:
            parts.append(f"{self.rain_chance}% chance of rain")
        if self.wind_max is not None:
            parts.append(f"wind up to {self.wind_max:.0f} {self.wind_unit}")
        if self.uv_max is not None and self.uv_max >= 6:
            parts.append(f"high UV ({self.uv_max:.0f})")
        if self.sunset:
            parts.append(f"sunset {self.sunset}")
        return ", ".join(parts)


def _clock(iso: str | None) -> str | None:
    return iso.split("T", 1)[1][:5] if iso and "T" in iso else None


def parse_forecast(data: dict, fahrenheit: bool) -> Forecast:
    """Turns Open-Meteo's `daily` block (one day requested) into a Forecast."""
    daily = data["daily"]

    def first(key: str):
        values = daily.get(key)
        return values[0] if values else None

    code = int(first("weather_code") or 0)
    return Forecast(
        day=first("time"),
        summary=describe_code(code),
        emoji=emoji_for(code),
        high=float(first("temperature_2m_max")),
        low=float(first("temperature_2m_min")),
        unit="°F" if fahrenheit else "°C",
        rain_chance=first("precipitation_probability_max"),
        wind_max=first("wind_speed_10m_max"),
        wind_unit="mph" if fahrenheit else "km/h",
        uv_max=first("uv_index_max"),
        sunrise=_clock(first("sunrise")),
        sunset=_clock(first("sunset")),
    )


def fetch_forecast(lat: float, lon: float, tz: str, day: date, fahrenheit: bool) -> Forecast | None:
    """Today's forecast for a place, or None if the service is unavailable (the reading goes on without it)."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": tz,
        "start_date": day.isoformat(),
        "end_date": day.isoformat(),
        "daily": ",".join(
            [
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
                "wind_speed_10m_max",
                "uv_index_max",
                "sunrise",
                "sunset",
            ]
        ),  # fmt: skip
        "temperature_unit": "fahrenheit" if fahrenheit else "celsius",
        "wind_speed_unit": "mph" if fahrenheit else "kmh",
    }
    try:
        response = httpx.get(FORECAST_URL, params=params, timeout=TIMEOUT)
        data = response.json()
        if response.status_code != 200 or data.get("error"):
            log.warning("[weather] forecast unavailable (%s): %s", response.status_code, data.get("reason"))
            return None
        return parse_forecast(data, fahrenheit)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as err:
        log.warning("[weather] forecast failed: %s", err)
        return None


@dataclass
class Place:
    label: str  # "Austin, Texas, United States"
    lat: float
    lon: float
    tz: str
    country_code: str


def parse_places(data: dict) -> list[Place]:
    places = []
    for r in data.get("results") or []:
        if not r.get("timezone"):
            continue
        label = ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x)
        places.append(
            Place(label, float(r["latitude"]), float(r["longitude"]), r["timezone"], r.get("country_code", ""))
        )
    return places


US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}  # fmt: skip
COUNTRY_ALIASES = {"USA": "United States", "US": "United States", "UK": "United Kingdom"}


def _expand(qualifier: str) -> str:
    q = qualifier.strip().strip(".")
    return (US_STATES.get(q.upper()) or COUNTRY_ALIASES.get(q.upper()) or q).lower()


def split_query(query: str) -> tuple[str, list[str]]:
    """'Austin, TX' -> ('Austin', ['texas']): the city name, plus qualifiers to rank matches with."""
    name, *rest = [part.strip() for part in query.split(",")]
    return _fix_saint(name), [_expand(q) for q in rest if q.strip()]


def _fix_saint(name: str) -> str:
    """'St.Louis' and 'St Louis' -> 'St. Louis', the spelling the geocoder knows."""
    return re.sub(r"^st(?:\.\s*|\s+)(?=\w)", "St. ", name, flags=re.IGNORECASE)


def name_candidates(query: str) -> list[tuple[str, list[str]]]:
    """Ways to read a query, best first. The geocoder only matches whole place names, so for
    'Barrington Illinois' (no comma) it finds nothing; then try 'Barrington' ranked by 'illinois'."""
    name, qualifiers = split_query(query)
    candidates = [(name, qualifiers)]
    words = name.split()
    for k in range(len(words) - 1, 0, -1):
        shorter = " ".join(words[:k])
        if len(shorter) >= 2:
            candidates.append((shorter, [_expand(w) for w in words[k:]] + qualifiers))
    return candidates[:4]


def rank_places(places: list[Place], name: str, qualifiers: list[str], count: int = 5) -> list[Place]:
    """Puts matches for the typed state/country first, then exact name matches; drops duplicates."""

    def score(p: Place) -> tuple[int, int]:
        parts = [x.strip().lower() for x in p.label.split(",")]
        region = parts[1:]
        hits = sum(any(r.startswith(q) or q in r.split() for r in region) for q in qualifiers)
        exact = parts[0] == name.lower()
        return (-hits, 0 if exact else 1)

    seen, out = set(), []
    for p in sorted(places, key=score):  # sorted() is stable, so the service's own order breaks ties
        if p.label not in seen:
            seen.add(p.label)
            out.append(p)
    return out[:count]


class SearchUnavailable(Exception):
    """The place-search service didn't answer, as opposed to answering with no matches."""


def _geocode(name: str) -> list[Place]:
    params = {"name": name, "count": 20, "language": "en", "format": "json"}
    try:
        response = httpx.get(GEOCODE_URL, params=params, timeout=TIMEOUT)
    except httpx.TransportError:
        # One retry for a dropped or slow connection (not for error responses like a 429).
        response = httpx.get(GEOCODE_URL, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return parse_places(response.json())


def search_places(query: str, count: int = 5, geocode=_geocode) -> list[Place]:
    """Looks up a city by name. Each match comes with its timezone, which the birth chart needs.
    Raises SearchUnavailable if the service can't be reached or refuses (e.g. rate-limited)."""
    try:
        for name, qualifiers in name_candidates(query.strip()):
            if len(name) < 2:
                continue
            places = geocode(name)
            if places:
                return rank_places(places, name, qualifiers, count)
        return []
    except (httpx.HTTPError, ValueError) as err:
        detail = err.response.text[:200] if isinstance(err, httpx.HTTPStatusError) else ""
        log.warning("[weather] place search failed: %s %s", err, detail)
        raise SearchUnavailable(str(err)) from err
