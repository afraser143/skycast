import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app import astro, horoscope, nature, weather

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


def reading(text="The rain softens the ground. Let your plans soak in.", focus="Soft Ground", **kw):
    return horoscope.Reading(
        focus=focus, horoscope=text, messenger=kw.get("messenger", "Heron"),
        messenger_message=kw.get("message", "Stand still and let what you need come to you."),
    )  # fmt: skip


@pytest.fixture
def facts():
    chart = astro.natal_chart(BIRTH, 30.27, -97.74)
    forecast = weather.parse_forecast(load("forecast_daily.json"), fahrenheit=True)
    return horoscope.day_facts(chart, datetime(2026, 11, 15, 18, tzinfo=UTC), forecast, lat=30.27)


def test_prompt_carries_the_sky_season_and_weather_but_not_the_birth_details(facts):
    prompt = facts.for_prompt()
    assert "Natal big three: Sun in Taurus" in prompt
    assert "Today's Moon:" in prompt and "Local weather: light rain" in prompt
    assert "Season where the reader lives: mid autumn" in prompt  # Nov 15: Oct 23 to Nov 22 is mid autumn
    for private in ("1990", "May 4", "14:30", "Austin", "30.27", "-97.74"):
        assert private not in prompt


def test_prompt_lists_only_the_strongest_transits(facts):
    lines = [line for line in facts.for_prompt().splitlines() if line.startswith("- transiting")]
    assert 0 < len(lines) <= 3 if facts.transits else lines == []


def test_system_prompt_sets_the_voice_and_lists_every_messenger():
    p = horoscope.SYSTEM_PROMPT
    assert "earth-wise guide" in p and "spirit animal" in p  # the voice, and what to avoid
    assert all(f"- {name} (" in p for name in nature.MESSENGER_NAMES)


def test_messenger_must_come_from_the_list():
    with pytest.raises(ValueError):
        reading(messenger="Unicorn")


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
    assert (horoscope.reading_issue(reading(text)) is None) == ok


def test_messenger_message_must_be_one_short_sentence():
    assert horoscope.reading_issue(reading(message="Be still. Then move.")) is not None
    assert horoscope.reading_issue(reading(message="x" * 200 + ".")) is not None


def test_good_ai_reading_is_used(facts):
    good = reading(focus=" Tender Rain ", messenger="Moss")
    result, source = horoscope.write_reading(facts, lambda f: good)
    assert source == "ai" and result.focus == "Tender Rain" and result.messenger == "Moss"


@pytest.mark.parametrize(
    "writer",
    [
        None,  # no API key
        lambda f: None,  # the call failed
        lambda f: reading(focus="Way Too Many Words In This Focus", text="Nope."),  # bad output
    ],
)
def test_falls_back_to_an_earth_wise_template(facts, writer):
    result, source = horoscope.write_reading(facts, writer)
    assert source == "template"
    assert horoscope.reading_issue(result) is None
    assert result.horoscope.startswith(nature.WEATHER_WISDOM["rain"])  # light rain in the fixture
    assert result.messenger in nature.MESSENGER_NAMES


def test_template_without_weather_or_transits(facts):
    facts.weather = None
    facts.transits = []
    result = horoscope.template_reading(facts)
    assert "mid autumn" in result.horoscope and "still pond" in result.horoscope
    assert result.focus == "Quiet Waters"
    assert horoscope.reading_issue(result) is None


# ---- Nature -----------------------------------------------------------------


@pytest.mark.parametrize(
    "sun_lon,lat,expected",
    [
        (5, 40, "early spring"),
        (100, 40, "early summer"),
        (235, 30, "mid autumn"),
        (260, 30, "late autumn"),
        (235, -33, "mid spring"),  # southern hemisphere: November is spring
        (300, 40, "mid winter"),
    ],
)
def test_seasons(sun_lon, lat, expected):
    assert nature.season(sun_lon, lat) == expected


def test_weather_conditions():
    assert nature.condition(None) == "clear"
    assert nature.condition(61) == "rain" and nature.condition(95) == "storm" and nature.condition(45) == "fog"


def test_every_fallback_messenger_choice_is_valid_and_stable():
    from datetime import date

    for cond in nature.CONDITIONS:
        for aspect in (*nature.ASPECT_WISDOM, None):
            first = nature.pick_messenger(cond, aspect, date(2026, 11, 15))
            assert first is nature.pick_messenger(cond, aspect, date(2026, 11, 15))
            assert cond in first.conditions or aspect in first.aspects
    rainy_square = nature.pick_messenger("rain", "square", date(2026, 11, 15))
    assert "rain" in rainy_square.conditions and "square" in rainy_square.aspects


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


def _fake_geocoder(known: dict[str, str]):
    """Answers like the real service: whole-name matches only."""
    calls = []

    def geocode(name):
        calls.append(name)
        # The real service ignores case.
        known_lower = {k.lower(): v for k, v in known.items()}
        return weather.parse_places(load(known_lower[name.lower()])) if name.lower() in known_lower else []

    return geocode, calls


@pytest.mark.parametrize("query", ["Barrington Illinois", "Barrington IL", "barrington illinois USA", "Barrington, IL"])
def test_finds_a_town_typed_with_its_state_and_no_comma(query):
    geocode, calls = _fake_geocoder({"Barrington": "geocode_barrington.json"})
    places = weather.search_places(query, geocode=geocode)
    assert places[0].label == "Barrington, Illinois, United States"
    assert places[0].tz == "America/Chicago"
    assert calls[-1].lower() == "barrington" and len(calls) <= 4


def test_saint_spelling():
    assert weather.split_query("St.Louis")[0] == "St. Louis"
    assert weather.split_query("st Louis, MO") == ("St. Louis", ["missouri"])
    assert weather.split_query("Stockholm")[0] == "Stockholm"
    assert weather.split_query("Stanford")[0] == "Stanford"


def test_multi_word_qualifiers():
    assert weather.name_candidates("St.Louis Missouri USA")[2] == ("St. Louis", ["missouri", "united states"])


def test_no_match_anywhere_returns_nothing():
    geocode, calls = _fake_geocoder({})
    assert weather.search_places("Nowhereville Illinois", geocode=geocode) == []
