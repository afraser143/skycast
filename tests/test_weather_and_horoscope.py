import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app import astro, horoscope, weather

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


# ---- Weather ---------------------------------------------------------------


def test_parse_forecast():
    # Hand-written in Open-Meteo's documented `daily` format: the live API was rate-limited when this was built.
    f = weather.parse_forecast(load("forecast_daily.json"), fahrenheit=True)
    assert f.day == "2026-11-15"
    assert f.summary == "light rain" and f.emoji == "🌧️"
    assert (f.high, f.low, f.unit) == (68.4, 51.2, "°F")
    assert f.rain_chance == 70 and f.sunrise == "07:01" and f.sunset == "17:34"
    assert f.for_prompt() == "light rain, high 68°F / low 51°F, 70% chance of rain, wind up to 12 mph, sunset 17:34"


def test_parse_places_from_a_real_geocoding_response():
    places = weather.parse_places(load("geocode_austin.json"))
    assert places[0].label == "Austin, Texas, United States"
    assert places[0].tz == "America/Chicago"
    assert places[0].country_code == "US"


def test_unknown_weather_codes_still_describe_something():
    assert weather.describe_code(1234) == "mixed weather"
    assert weather.emoji_for(63) == "🌧️" and weather.emoji_for(73) == "🌨️"


def test_forecast_failure_returns_none(monkeypatch):
    class Boom:
        status_code = 429

        def json(self):
            return {"error": True, "reason": "Daily API request limit exceeded."}

    monkeypatch.setattr(weather.httpx, "get", lambda *a, **k: Boom())
    assert weather.fetch_forecast(30.27, -97.74, "America/Chicago", date(2026, 11, 15), True) is None


# ---- Horoscope -------------------------------------------------------------

BIRTH = datetime(1990, 5, 4, 14, 30, tzinfo=UTC)


@pytest.fixture
def facts():
    chart = astro.natal_chart(BIRTH, 30.27, -97.74)
    forecast = weather.parse_forecast(load("forecast_daily.json"), fahrenheit=True)
    return horoscope.day_facts(chart, datetime(2026, 11, 15, 18, tzinfo=UTC), forecast)


def test_prompt_carries_the_sky_and_weather_but_not_the_birth_details(facts):
    prompt = facts.for_prompt()
    assert "Natal big three: Sun in Taurus" in prompt
    assert "Today's Moon:" in prompt and "Local weather: light rain" in prompt
    for private in ("1990", "May 4", "14:30", "Austin", "30.27", "-97.74"):
        assert private not in prompt


def test_prompt_lists_only_the_strongest_transits(facts):
    lines = [line for line in facts.for_prompt().splitlines() if line.startswith("- transiting")]
    assert 0 < len(lines) <= 3 if facts.transits else lines == []


@pytest.mark.parametrize(
    "text,ok",
    [
        ("One sentence only.", False),
        ("The Moon lights your Venus. Rain says stay in.", True),
        ("One. Two. Three.", True),
        ("One. Two. Three. Four.", False),
        ("Long. " + "word " * 130 + "end.", False),
    ],
)
def test_reading_validation(text, ok):
    assert (horoscope.reading_issue(horoscope.Reading(focus="Quiet Strength", horoscope=text)) is None) == ok


def test_good_ai_reading_is_used(facts):
    good = horoscope.Reading(
        focus=" Tender Rain ", horoscope="The Moon softens your Venus today. Let the rain slow you down."
    )
    reading, source = horoscope.write_reading(facts, lambda f: good)
    assert source == "ai" and reading.focus == "Tender Rain"


@pytest.mark.parametrize(
    "writer",
    [
        None,  # no API key
        lambda f: None,  # the call failed
        lambda f: horoscope.Reading(focus="Way Too Many Words In This Focus", horoscope="Nope."),  # bad output
    ],
)
def test_falls_back_to_a_template_from_the_facts(facts, writer):
    reading, source = horoscope.write_reading(facts, writer)
    assert source == "template"
    assert horoscope.reading_issue(reading) is None
    assert facts.moon_sign in reading.horoscope
    assert "light rain" in reading.horoscope


def test_template_without_weather_or_transits(facts):
    facts.weather = None
    facts.transits = []
    reading = horoscope.template_reading(facts)
    assert reading.focus == "Quiet Sky"
    assert horoscope.reading_issue(reading) is None


@pytest.mark.parametrize(
    "query,first",
    [
        ("Springfield, IL", "Springfield, Illinois, United States"),
        ("Springfield, Massachusetts", "Springfield, Massachusetts, United States"),
        ("springfield, mo", "Springfield, Missouri, United States"),
    ],
)
def test_typed_state_ranks_the_right_city_first(query, first):
    # A real Open-Meteo response for "Springfield", which ignores anything after the comma.
    places = weather.parse_places(load("geocode_springfield.json"))
    name, qualifiers = weather.split_query(query)
    ranked = weather.rank_places(places, name, qualifiers)
    assert ranked[0].label == first
    assert len({p.label for p in ranked}) == len(ranked) <= 5


def test_split_query_expands_abbreviations():
    assert weather.split_query("Austin, TX") == ("Austin", ["texas"])
    assert weather.split_query("London, UK") == ("London", ["united kingdom"])
    assert weather.split_query("Paris") == ("Paris", [])
