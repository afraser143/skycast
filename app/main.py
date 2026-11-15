"""Skycast: a daily horoscope from your real birth chart and your local weather."""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import time
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import astro, horoscope, readings, weather
from .db import Profile, make_engine, session_factory
from .wheel import chart_svg

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("skycast")

HERE = Path(__file__).parent
ON_VERCEL = bool(os.environ.get("VERCEL"))


def _session_secret() -> str:
    secret = os.environ.get("SESSION_SECRET")
    if secret:
        return secret
    if ON_VERCEL:
        raise RuntimeError("SESSION_SECRET must be set when running on Vercel")
    return secrets.token_urlsafe(32)  # local dev: sessions last until the server restarts


def create_app() -> FastAPI:
    app = FastAPI(title="Skycast", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(),
        session_cookie="skycast",
        max_age=60 * 60 * 24 * 365,  # stay signed in on the home-screen app for a year
        same_site="lax",
        https_only=ON_VERCEL,
    )
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    # Swappable in tests.
    app.state.fetch_forecast = weather.fetch_forecast
    app.state.search_places = weather.search_places
    app.state.writer = horoscope.claude_writer if horoscope.ai_enabled() else None
    app.state.sessions = None
    return app


app = create_app()
templates = Jinja2Templates(directory=HERE / "templates")


# ---- Plumbing ------------------------------------------------------------------


def get_db(request: Request):
    state = request.app.state
    if state.sessions is None:
        state.sessions = session_factory(make_engine())
    with state.sessions() as session:
        yield session


def app_password() -> str | None:
    password = os.environ.get("APP_PASSWORD")
    if not password and ON_VERCEL:
        raise RuntimeError("APP_PASSWORD must be set when running on Vercel")
    return password


class LoginRequired(Exception):
    pass


class SetupRequired(Exception):
    pass


@app.exception_handler(LoginRequired)
async def _to_login(_request: Request, _exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(SetupRequired)
async def _to_setup(_request: Request, _exc: SetupRequired):
    return RedirectResponse("/setup", status_code=303)


def require_login(request: Request) -> None:
    # With no APP_PASSWORD (local development only) the app is open.
    if app_password() and not request.session.get("ok"):
        raise LoginRequired()


def require_profile(db: Session = Depends(get_db), _: None = Depends(require_login)) -> Profile:
    profile = db.get(Profile, 1)
    if profile is None:
        raise SetupRequired()
    return profile


def render(request: Request, name: str, **context) -> HTMLResponse:
    context.setdefault("protected", bool(app_password()))
    return templates.TemplateResponse(request, name, context)


# ---- Sign in -------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not app_password():
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", error=None)


@app.post("/login")
def login(request: Request, password: str = Form(...)):
    expected = app_password()
    if expected and hmac.compare_digest(password.encode(), expected.encode()):
        request.session["ok"] = True
        return RedirectResponse("/", status_code=303)
    time.sleep(1)  # slow down guessing
    return render(request, "login.html", error="That's not it. Try again.")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---- Setup ---------------------------------------------------------------------


def _place_value(p: weather.Place) -> str:
    return f"{p.lat}|{p.lon}|{p.tz}|{p.country_code}|{p.label}"


def _parse_place_value(value: str) -> weather.Place:
    try:
        lat, lon, tz, cc, label = value.split("|", 4)
        ZoneInfo(tz)
        return weather.Place(label[:200], float(lat), float(lon), tz, cc)
    except (ValueError, ZoneInfoNotFoundError) as err:
        raise HTTPException(400, "Pick a place from the list") from err


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, db: Session = Depends(get_db), _: None = Depends(require_login)):
    return render(request, "setup.html", profile=db.get(Profile, 1), error=None, form={})


@app.post("/setup", response_class=HTMLResponse)
def setup_search(
    request: Request,
    birth_date: str = Form(...),
    birth_time: str = Form(...),
    birth_place: str = Form(...),
    home_city: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_login),
):
    """Step 1: look up both places, then let the user pick the right match for each."""
    form = {"birth_date": birth_date, "birth_time": birth_time, "birth_place": birth_place, "home_city": home_city}
    try:
        born = datetime.fromisoformat(f"{birth_date}T{birth_time}")
    except ValueError:
        return render(
            request, "setup.html", profile=db.get(Profile, 1), error="Check the birth date and time.", form=form
        )
    if not 1900 <= born.year <= date.today().year:
        return render(request, "setup.html", profile=db.get(Profile, 1), error="Check the birth year.", form=form)

    search = request.app.state.search_places
    birth_matches, home_matches = search(birth_place), search(home_city)
    if not birth_matches or not home_matches:
        missing = "birth place" if not birth_matches else "home city"
        error = f"Couldn't find that {missing}. Try adding the state or country."
        return render(request, "setup.html", profile=db.get(Profile, 1), error=error, form=form)
    return render(
        request,
        "setup_confirm.html",
        birth_local=born.strftime("%Y-%m-%dT%H:%M"),
        born=born,
        birth_matches=[(_place_value(p), p) for p in birth_matches],
        home_matches=[(_place_value(p), p) for p in home_matches],
    )


@app.post("/setup/confirm")
def setup_confirm(
    birth_local: str = Form(...),
    birth_choice: str = Form(...),
    home_choice: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_login),
):
    """Step 2: save. Converting the local birth time to UTC uses the birthplace's historical time-zone rules."""
    birth = _parse_place_value(birth_choice)
    home = _parse_place_value(home_choice)
    try:
        local = datetime.fromisoformat(birth_local).replace(tzinfo=ZoneInfo(birth.tz))
    except ValueError as err:
        raise HTTPException(400, "Bad birth time") from err

    profile = db.get(Profile, 1) or Profile(id=1)
    profile.birth_utc = local.astimezone(UTC)
    profile.birth_local = birth_local
    profile.birth_label, profile.birth_lat, profile.birth_lon, profile.birth_tz = (
        birth.label, birth.lat, birth.lon, birth.tz,
    )  # fmt: skip
    profile.home_label, profile.home_lat, profile.home_lon, profile.home_tz = home.label, home.lat, home.lon, home.tz
    # The US (and a few others) use Fahrenheit.
    profile.fahrenheit = home.country_code in {"US", "LR", "PR", "GU", "BS", "KY", "PW", "FM", "MH"}
    db.merge(profile)
    # A new chart or city means old readings no longer match, so start fresh from today.
    db.query(readings.DailyReading).delete()
    db.commit()
    return RedirectResponse("/chart", status_code=303)


# ---- Pages ---------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def today(request: Request, profile: Profile = Depends(require_profile), db: Session = Depends(get_db)):
    state = request.app.state
    reading = readings.todays_reading(db, profile, state.fetch_forecast, state.writer)
    return render(request, "today.html", reading=reading, facts=reading.facts, profile=profile, tab="today")


@app.get("/chart", response_class=HTMLResponse)
def chart_page(request: Request, profile: Profile = Depends(require_profile)):
    chart = readings.natal_chart(profile)
    return render(
        request,
        "chart.html",
        chart=chart,
        wheel=chart_svg(chart),
        profile=profile,
        born=datetime.fromisoformat(profile.birth_local),
        signs=astro.SIGNS,
        tab="chart",
    )


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request, _profile: Profile = Depends(require_profile), db: Session = Depends(get_db)):
    return render(request, "history.html", days=readings.history(db), tab="history")


@app.get("/healthz")
def healthz() -> Response:
    return Response("ok", media_type="text/plain")
