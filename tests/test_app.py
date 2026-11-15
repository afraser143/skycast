"""End-to-end through the web app, with weather, place search and the AI swapped for fakes."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import horoscope, weather
from app.db import DailyReading, Profile, make_engine, session_factory
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
PLACES = weather.parse_places(json.loads((FIXTURES / "geocode_austin.json").read_text()))
FORECAST = weather.parse_forecast(json.loads((FIXTURES / "forecast_daily.json").read_text()), fahrenheit=True)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_PASSWORD", "stargazer")
    monkeypatch.setattr("app.main.time.sleep", lambda s: None)
    calls = {"writer": 0, "forecast": 0}

    def writer(facts):
        calls["writer"] += 1
        return horoscope.Reading(focus="Clear Signal", horoscope="The Moon is on your side today. Carry an umbrella.")

    def forecast(*args):
        calls["forecast"] += 1
        return FORECAST

    app.state.sessions = session_factory(make_engine(f"sqlite:///{tmp_path / 'test.db'}"))
    app.state.search_places = lambda q: PLACES if q else []
    app.state.fetch_forecast = forecast
    app.state.writer = writer
    with TestClient(app) as c:
        c.calls = calls
        yield c


def login(c):
    r = c.post("/login", data={"password": "stargazer"}, follow_redirects=False)
    assert r.status_code == 303


def setup_profile(c):
    r = c.post(
        "/setup",
        data={"birth_date": "1990-05-04", "birth_time": "09:30", "birth_place": "Austin", "home_city": "Austin"},
    )
    assert "Which ones?" in r.text and "Austin, Texas, United States" in r.text
    choice = f"{PLACES[0].lat}|{PLACES[0].lon}|{PLACES[0].tz}|{PLACES[0].country_code}|{PLACES[0].label}"
    r = c.post(
        "/setup/confirm",
        data={"birth_local": "1990-05-04T09:30", "birth_choice": choice, "home_choice": choice},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/chart"


def test_everything_needs_the_password(client):
    for path in ("/", "/chart", "/history", "/setup"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login", path
    r = client.post("/login", data={"password": "wrong"})
    assert "not it" in r.text


def test_setup_then_chart(client):
    login(client)
    assert client.get("/", follow_redirects=False).headers["location"] == "/setup"
    setup_profile(client)

    with app.state.sessions() as db:
        profile = db.get(Profile, 1)
        # 9:30 in Austin in May is CDT (UTC-5), so 14:30 UTC.
        assert profile.birth_utc.strftime("%H:%M") == "14:30"
        assert profile.fahrenheit

    r = client.get("/chart")
    assert r.status_code == 200
    assert "Taurus" in r.text  # Sun sign for May 4
    assert "<svg" in r.text and "Placements" in r.text


def test_today_is_written_once_and_saved(client):
    login(client)
    setup_profile(client)

    first = client.get("/")
    assert "The Moon is on your side today." in first.text
    assert "Light rain" in first.text and "Written by Claude" in first.text
    second = client.get("/")
    assert second.text == first.text
    assert client.calls == {"writer": 1, "forecast": 1}

    history = client.get("/history")
    assert "Clear Signal" in history.text


def test_without_ai_or_weather_today_still_works(client):
    app.state.writer = None
    app.state.fetch_forecast = lambda *a: None
    login(client)
    setup_profile(client)
    r = client.get("/")
    assert r.status_code == 200
    assert "Built from today's sky" in r.text and "Weather was unavailable" in r.text
    with app.state.sessions() as db:
        assert db.query(DailyReading).one().source == "template"


def test_bad_setup_input(client):
    login(client)
    r = client.post(
        "/setup", data={"birth_date": "1990-05-04", "birth_time": "9:30pm", "birth_place": "x", "home_city": "y"}
    )
    assert "Check the birth date and time" in r.text
    r = client.post(
        "/setup", data={"birth_date": "1990-05-04", "birth_time": "09:30", "birth_place": "", "home_city": "y"}
    )
    assert r.status_code == 422 or "Couldn" in r.text
    r = client.post(
        "/setup/confirm", data={"birth_local": "1990-05-04T09:30", "birth_choice": "junk", "home_choice": "junk"}
    )
    assert r.status_code == 400


def test_search_outage_is_reported_as_an_outage_not_a_missing_place(client):
    def down(q):
        raise weather.SearchUnavailable("429 Too Many Requests")

    app.state.search_places = down
    login(client)
    r = client.post(
        "/setup",
        data={
            "birth_date": "1990-05-04",
            "birth_time": "09:30",
            "birth_place": "Barrington, IL",
            "home_city": "Austin",
        },
    )
    assert "answering right now" in r.text
    assert "Couldn't find" not in r.text
