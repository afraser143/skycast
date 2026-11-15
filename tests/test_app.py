"""End-to-end through the web app, with weather, place search and the AI swapped for fakes."""

import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import horoscope, weather
from app.db import Person, Reading, make_engine, session_factory
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
AUSTIN = weather.parse_places(json.loads((FIXTURES / "geocode_austin.json").read_text()))
BARRINGTON = weather.parse_places(json.loads((FIXTURES / "geocode_barrington.json").read_text()))
FORECAST = weather.parse_forecast(json.loads((FIXTURES / "forecast_daily.json").read_text()), fahrenheit=True)


def value(p: weather.Place) -> str:
    return f"{p.lat}|{p.lon}|{p.tz}|{p.country_code}|{p.label}"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_PASSWORD", "stargazer")
    monkeypatch.setattr("app.main.time.sleep", lambda s: None)
    calls = {"writer": 0, "forecast": [], "prompts": []}

    def writer(facts):
        calls["writer"] += 1
        calls["prompts"].append(facts.for_prompt())
        return horoscope.Reading(
            focus="Clear Signal",
            horoscope="The Moon is on your side today. Carry an umbrella.",
            messenger="Heron",
            messenger_message="Stand still and let what you need come to you.",
        )

    def forecast(lat, lon, tz, day, fahrenheit):
        calls["forecast"].append(tz)
        return FORECAST

    app.state.sessions = session_factory(make_engine(f"sqlite:///{tmp_path / 'test.db'}"))
    app.state.search_places = lambda q: (BARRINGTON if "barrington" in q.lower() else AUSTIN) if q else []
    app.state.fetch_forecast = forecast
    app.state.writer = writer
    with TestClient(app) as c:
        c.calls = calls
        yield c


def login(c):
    r = c.post("/login", data={"password": "stargazer"}, follow_redirects=False)
    assert r.status_code == 303


def add_person(c, name="", born="1990-05-04T09:30", birth=AUSTIN[0], home=AUSTIN[0], person_id=None) -> int:
    form = {
        "name": name,
        "birth_date": born[:10],
        "birth_time": born[11:],
        "birth_place": birth.label,
        "home_city": home.label,
    }
    if person_id:
        form["person_id"] = person_id
    r = c.post("/people/search", data=form)
    assert "Which ones?" in r.text, r.text
    save = {"name": name, "birth_local": born, "birth_choice": value(birth), "home_choice": value(home)}
    if person_id:
        save["person_id"] = person_id
    r = c.post("/people/save", data=save, follow_redirects=False)
    match = re.fullmatch(r"/p/(\d+)/chart", r.headers["location"])
    assert match, r.headers["location"]
    return int(match.group(1))


def test_everything_needs_the_password(client):
    for path in ("/", "/p/1", "/p/1/chart", "/p/1/history", "/people/new", "/chart"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login", path
    assert client.post("/p/1/share", follow_redirects=False).headers["location"] == "/login"
    r = client.post("/login", data={"password": "wrong"})
    assert "not it" in r.text


def test_first_person_is_the_owner(client):
    login(client)
    assert client.get("/", follow_redirects=False).headers["location"] == "/people/new"
    me = add_person(client)
    with app.state.sessions() as db:
        person = db.get(Person, me)
        assert person.is_owner and person.name == "Me"
        # 9:30 in Austin in May is CDT (UTC-5), so 14:30 UTC.
        assert person.birth_utc.strftime("%H:%M") == "14:30"
    assert client.get("/", follow_redirects=False).headers["location"] == f"/p/{me}"
    r = client.get(f"/p/{me}/chart")
    assert "Your sky at birth" in r.text and "Taurus" in r.text and "<svg" in r.text


def test_today_is_written_once_per_person_per_day(client):
    login(client)
    me = add_person(client)
    first = client.get(f"/p/{me}")
    assert "The Moon is on your side today." in first.text and "Light rain" in first.text
    assert client.get(f"/p/{me}").text == first.text
    assert client.calls["writer"] == 1
    assert "Clear Signal" in client.get(f"/p/{me}/history").text


def test_each_person_gets_their_own_chart_city_and_reading(client):
    login(client)
    me = add_person(client)
    sam = add_person(client, name="Sam Rivera", born="1992-11-20T22:15", birth=BARRINGTON[1], home=BARRINGTON[1])

    r = client.get(f"/p/{sam}")
    assert "Barrington" in r.text  # weather label is Sam's city
    assert ">Me<" in r.text and ">Sam<" in r.text  # switcher shows both people
    client.get(f"/p/{me}")
    assert client.calls["writer"] == 2
    assert client.calls["forecast"] == ["America/Chicago", "America/Chicago"]

    chart = client.get(f"/p/{sam}/chart").text
    assert "Sam's sky at birth" in chart and "Scorpio" in chart  # Sun sign for Nov 20
    # Names never reach the AI: it only gets the facts.
    assert not any("Sam" in p for p in client.calls["prompts"])


def test_share_link_shows_one_person_and_nothing_else(client):
    login(client)
    add_person(client)
    sam = add_person(client, name="Sam", born="1992-11-20T22:15", birth=BARRINGTON[1], home=BARRINGTON[1])

    page = client.post(f"/p/{sam}/share").text
    link = re.search(r'value="(http[^"]+/s/([^"]+))"', page)
    assert link, page
    token = link.group(2)
    with app.state.sessions() as db:
        assert token not in db.get(Person, sam).share_hash  # only a hash is stored

    stranger = TestClient(app)  # no password, no session
    r = stranger.get(f"/s/{token}")
    assert r.status_code == 200
    assert "Sam's sky" in r.text and "The Moon is on your side today." in r.text
    assert r.headers["x-robots-tag"].startswith("noindex") and r.headers["referrer-policy"] == "no-referrer"
    assert ">Me<" not in r.text and "/people/new" not in r.text and "/edit" not in r.text and "/share" not in r.text
    assert "Sam's sky at birth" in stranger.get(f"/s/{token}/chart").text
    assert stranger.get(f"/s/{token}/history").status_code == 200
    # The link can't reach anyone else, or the owner pages.
    assert stranger.get(f"/p/{sam}", follow_redirects=False).headers["location"] == "/login"

    # A new link replaces the old one; turning it off kills it.
    page = client.post(f"/p/{sam}/share").text
    new_token = re.search(r'/s/([^"]+)"', page).group(1)
    assert stranger.get(f"/s/{token}").status_code == 404
    assert stranger.get(f"/s/{new_token}").status_code == 200
    client.post(f"/p/{sam}/share/off")
    assert stranger.get(f"/s/{new_token}").status_code == 404
    assert stranger.get("/s/made-up-token").status_code == 404


def test_remove_someone_but_not_yourself(client):
    login(client)
    me = add_person(client)
    sam = add_person(client, name="Sam")
    client.get(f"/p/{sam}")
    assert client.post(f"/p/{me}/delete").status_code == 400
    client.post(f"/p/{sam}/delete")
    assert client.get(f"/p/{sam}").status_code == 404
    with app.state.sessions() as db:
        assert db.query(Reading).filter(Reading.person_id == sam).count() == 0


def test_editing_resets_that_persons_readings_only(client):
    login(client)
    me = add_person(client)
    sam = add_person(client, name="Sam")
    client.get(f"/p/{me}")
    client.get(f"/p/{sam}")
    add_person(client, name="Sam", born="1992-11-21T22:15", person_id=sam)
    with app.state.sessions() as db:
        assert db.query(Reading).filter(Reading.person_id == sam).count() == 0
        assert db.query(Reading).filter(Reading.person_id == me).count() == 1


def test_without_ai_or_weather_today_still_works(client):
    app.state.writer = None
    app.state.fetch_forecast = lambda *a: None
    login(client)
    me = add_person(client)
    r = client.get(f"/p/{me}")
    assert "Built from today's sky" in r.text and "weather was unavailable" in r.text
    assert "Today's messenger" in r.text  # the fallback still picks a messenger


def test_bad_input(client):
    login(client)
    add_person(client)
    form = {"name": "Sam", "birth_date": "1990-05-04", "birth_time": "9:30pm", "birth_place": "x", "home_city": "y"}
    assert "Check the birth date and time" in client.post("/people/search", data=form).text
    form |= {"birth_time": "09:30", "name": " "}
    assert "Add a name" in client.post("/people/search", data=form).text
    r = client.post(
        "/people/save",
        data={"name": "x", "birth_local": "1990-05-04T09:30", "birth_choice": "junk", "home_choice": "junk"},
    )
    assert r.status_code == 400


def test_search_outage_is_reported_as_an_outage_not_a_missing_place(client):
    def down(q):
        raise weather.SearchUnavailable("429 Too Many Requests")

    app.state.search_places = down
    login(client)
    form = {"birth_date": "1990-05-04", "birth_time": "09:30", "birth_place": "Barrington, IL", "home_city": "Austin"}
    r = client.post("/people/search", data=form)
    assert "answering right now" in r.text and "Couldn't find" not in r.text


def test_old_addresses_redirect(client):
    login(client)
    me = add_person(client)
    assert client.get("/chart", follow_redirects=False).headers["location"] == f"/p/{me}/chart"
    assert client.get("/setup", follow_redirects=False).headers["location"] == f"/p/{me}/edit"


def test_favicon_is_served_without_signing_in(client):
    for path in ("/favicon.ico", "/favicon.png"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_upgrade_copies_the_single_user_database(tmp_path):
    """A database from before multiple people: one `profile` row and its `readings`."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE profile (id INTEGER PRIMARY KEY, birth_utc DATETIME, birth_local VARCHAR(32),
            birth_label VARCHAR(200), birth_lat FLOAT, birth_lon FLOAT, birth_tz VARCHAR(64),
            home_label VARCHAR(200), home_lat FLOAT, home_lon FLOAT, home_tz VARCHAR(64), fahrenheit BOOLEAN);
        CREATE TABLE readings (day DATE PRIMARY KEY, focus VARCHAR(80), horoscope TEXT, source VARCHAR(16),
            facts JSON, created_at DATETIME);
        INSERT INTO profile VALUES (1, '1990-05-04 14:30:00.000000', '1990-05-04T09:30',
            'Barrington, Illinois, United States', 42.15, -88.13, 'America/Chicago',
            'Austin, Texas, United States', 30.27, -97.74, 'America/Chicago', 1);
        INSERT INTO readings VALUES ('2026-11-14', 'Old Focus', 'Yesterday was good. Today too.', 'ai',
            '{"moon_sign": "Aries", "moon_emoji": "🌒"}', '2026-11-14 12:00:00');
        """
    )
    con.commit()
    con.close()

    sessions = session_factory(make_engine(f"sqlite:///{path}"))
    with sessions() as db:
        (me,) = db.query(Person).all()
        assert me.is_owner and me.birth_label.startswith("Barrington") and me.birth_local == "1990-05-04T09:30"
        (old,) = db.query(Reading).all()
        assert old.person_id == me.id and old.focus == "Old Focus" and old.facts["moon_sign"] == "Aries"

    # Running it again (every cold start) changes nothing.
    sessions = session_factory(make_engine(f"sqlite:///{path}"))
    with sessions() as db:
        assert db.query(Person).count() == 1 and db.query(Reading).count() == 1


def test_today_shows_the_messenger_and_the_living_sky(client):
    login(client)
    me = add_person(client)
    r = client.get(f"/p/{me}")
    assert "The Heron" in r.text and "Stand still and let what you need come to you." in r.text
    assert 'data-condition="rain"' in r.text  # light rain in the forecast fixture
    assert re.search(r'data-period="(night|dawn|day|dusk)"', r.text)
    assert "moon-svg" in r.text
    with app.state.sessions() as db:
        saved = db.query(Reading).one()
        assert saved.messenger == "Heron" and saved.facts["season"].endswith(("autumn", "winter", "spring", "summer"))


def test_ask_again_rewrites_today_for_the_owner_only(client):
    login(client)
    me = add_person(client)
    client.get(f"/p/{me}")
    client.post(f"/p/{me}/again")
    client.get(f"/p/{me}")
    assert client.calls["writer"] == 2
    token = re.search(r"/s/([^\"]+)\"", client.post(f"/p/{me}/share").text).group(1)
    shared = TestClient(app).get(f"/s/{token}").text
    assert "Ask again" not in shared and "/again" not in shared
    assert TestClient(app).post(f"/p/{me}/again", follow_redirects=False).headers["location"] == "/login"


def test_new_columns_are_added_to_an_existing_table(tmp_path):
    """Readings saved before messengers existed: the table gains the columns and old rows still load."""
    path = tmp_path / "before.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE people (id INTEGER PRIMARY KEY, name VARCHAR(40), is_owner BOOLEAN, birth_utc DATETIME,
            birth_local VARCHAR(32), birth_label VARCHAR(200), birth_lat FLOAT, birth_lon FLOAT, birth_tz VARCHAR(64),
            home_label VARCHAR(200), home_lat FLOAT, home_lon FLOAT, home_tz VARCHAR(64), fahrenheit BOOLEAN,
            share_hash VARCHAR(64), share_created DATETIME, created_at DATETIME);
        CREATE TABLE person_readings (person_id INTEGER, day DATE, focus VARCHAR(80), horoscope TEXT,
            source VARCHAR(16), facts JSON, created_at DATETIME, PRIMARY KEY (person_id, day));
        INSERT INTO person_readings VALUES (1, '2026-11-14', 'Old', 'One. Two.', 'ai', '{}', '2026-11-14 12:00:00');
        """
    )
    con.commit()
    con.close()
    sessions = session_factory(make_engine(f"sqlite:///{path}"))
    with sessions() as db:
        old = db.query(Reading).one()
        assert old.focus == "Old" and old.messenger is None


def test_chart_page_reads_like_a_chart_not_a_spreadsheet(client):
    login(client)
    me = add_person(client)
    r = client.get(f"/p/{me}/chart").text
    assert "Sun in Taurus" in r and "Your core self: steady and sensual, rooted like rich soil." in r
    assert "Ascendant in" in r and "How you meet the world" in r
    assert "12th house" in r or "1st house" in r
    assert 'class="aspect aspect-' in r  # natal aspect lines in the wheel
