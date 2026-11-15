"""Skycast: a daily horoscope from a real birth chart and local weather, for you and the people you add."""

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
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import readings, weather
from .db import Person, make_engine, new_share_link, person_for_token, session_factory
from .wheel import chart_svg

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("skycast")

HERE = Path(__file__).parent
ON_VERCEL = bool(os.environ.get("VERCEL"))
FAHRENHEIT_COUNTRIES = {"US", "LR", "PR", "GU", "BS", "KY", "PW", "FM", "MH"}


def _session_secret() -> str:
    secret = os.environ.get("SESSION_SECRET")
    if secret:
        return secret
    if ON_VERCEL:
        raise RuntimeError("SESSION_SECRET must be set when running on Vercel")
    return secrets.token_urlsafe(32)  # local dev: sessions last until the server restarts


def create_app() -> FastAPI:
    from . import horoscope

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


@app.exception_handler(LoginRequired)
async def _to_login(_request: Request, _exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


def require_login(request: Request) -> None:
    # With no APP_PASSWORD (local development only) the app is open.
    if app_password() and not request.session.get("ok"):
        raise LoginRequired()


def owner(db: Session) -> Person | None:
    return db.scalar(select(Person).where(Person.is_owner))


def everyone(db: Session) -> list[Person]:
    return list(db.scalars(select(Person).order_by(Person.is_owner.desc(), Person.created_at)))


def get_person(person_id: int, db: Session = Depends(get_db), _: None = Depends(require_login)) -> Person:
    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(404, "No such person")
    return person


def shared_person(token: str, db: Session = Depends(get_db)) -> Person:
    person = person_for_token(db, token)
    if person is None:
        # Same answer for a wrong, old or turned-off link, so links can't be probed.
        raise HTTPException(404, "This link isn't active")
    return person


def render(request: Request, template: str, status_code: int = 200, **context) -> HTMLResponse:
    context.setdefault("protected", bool(app_password()))
    context.setdefault("shared", False)
    response = templates.TemplateResponse(request, template, context, status_code=status_code)
    if context["shared"]:
        # Keep share links out of search engines and out of Referer headers.
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(StarletteHTTPException)  # also catches FastAPI's HTTPException and unknown URLs
async def _http_error(request: Request, exc: StarletteHTTPException):
    return render(request, "error.html", status_code=exc.status_code, message=exc.detail)


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


# ---- Adding and editing people -------------------------------------------------


def _place_value(p: weather.Place) -> str:
    return f"{p.lat}|{p.lon}|{p.tz}|{p.country_code}|{p.label}"


def _parse_place_value(value: str) -> weather.Place:
    try:
        lat, lon, tz, cc, label = value.split("|", 4)
        ZoneInfo(tz)
        return weather.Place(label[:200], float(lat), float(lon), tz, cc)
    except (ValueError, ZoneInfoNotFoundError) as err:
        raise HTTPException(400, "Pick a place from the list") from err


def _form_page(
    request: Request, person: Person | None, first: bool, error: str | None = None, form: dict | None = None
):
    return render(request, "setup.html", person=person, first=first, error=error, form=form or {})


@app.get("/people/new", response_class=HTMLResponse)
def new_person_page(request: Request, db: Session = Depends(get_db), _: None = Depends(require_login)):
    return _form_page(request, None, first=owner(db) is None)


@app.get("/p/{person_id}/edit", response_class=HTMLResponse)
def edit_person_page(request: Request, person: Person = Depends(get_person)):
    return _form_page(request, person, first=False)


@app.post("/people/search", response_class=HTMLResponse)
def search_places(
    request: Request,
    name: str = Form(""),
    birth_date: str = Form(...),
    birth_time: str = Form(...),
    birth_place: str = Form(...),
    home_city: str = Form(...),
    person_id: int | None = Form(None),
    db: Session = Depends(get_db),
    _: None = Depends(require_login),
):
    """Step 1: look up both places, then let the user pick the right match for each."""
    person = db.get(Person, person_id) if person_id else None
    first = owner(db) is None
    name = (name.strip() or ("Me" if first or (person and person.is_owner) else ""))[:40]
    form = {
        "name": name,
        "birth_date": birth_date,
        "birth_time": birth_time,
        "birth_place": birth_place,
        "home_city": home_city,
    }

    def again(error: str):
        return _form_page(request, person, first, error, form)

    if not name:
        return again("Add a name.")
    try:
        born = datetime.fromisoformat(f"{birth_date}T{birth_time}")
    except ValueError:
        return again("Check the birth date and time.")
    if not 1900 <= born.year <= date.today().year:
        return again("Check the birth year.")

    search = request.app.state.search_places
    try:
        birth_matches, home_matches = search(birth_place), search(home_city)
    except weather.SearchUnavailable:
        return again("The place lookup service isn't answering right now. Wait a minute and try again.")
    if not birth_matches or not home_matches:
        missing = "birth place" if not birth_matches else "home city"
        return again(f"Couldn't find that {missing}. Try adding the state or country.")
    return render(
        request,
        "setup_confirm.html",
        name=name,
        person=person,
        birth_local=born.strftime("%Y-%m-%dT%H:%M"),
        born=born,
        birth_matches=[(_place_value(p), p) for p in birth_matches],
        home_matches=[(_place_value(p), p) for p in home_matches],
    )


@app.post("/people/save")
def save_person(
    name: str = Form(""),
    birth_local: str = Form(...),
    birth_choice: str = Form(...),
    home_choice: str = Form(...),
    person_id: int | None = Form(None),
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

    if person_id:
        person = db.get(Person, person_id)
        if person is None:
            raise HTTPException(404, "No such person")
    else:
        person = Person(is_owner=owner(db) is None)
        db.add(person)

    person.name = name.strip()[:40] or "Me"
    person.birth_utc = local.astimezone(UTC)
    person.birth_local = birth_local
    person.birth_label, person.birth_lat, person.birth_lon, person.birth_tz = (
        birth.label, birth.lat, birth.lon, birth.tz,
    )  # fmt: skip
    person.home_label, person.home_lat, person.home_lon, person.home_tz = home.label, home.lat, home.lon, home.tz
    person.fahrenheit = home.country_code in FAHRENHEIT_COUNTRIES
    db.flush()
    # A new chart or city means old readings no longer match, so start fresh from today.
    db.query(readings.Reading).filter(readings.Reading.person_id == person.id).delete()
    db.commit()
    return RedirectResponse(f"/p/{person.id}/chart", status_code=303)


@app.post("/p/{person_id}/delete")
def delete_person(person: Person = Depends(get_person), db: Session = Depends(get_db)):
    if person.is_owner:
        raise HTTPException(400, "You can't remove yourself")
    db.query(readings.Reading).filter(readings.Reading.person_id == person.id).delete()
    db.delete(person)
    db.commit()
    return RedirectResponse("/", status_code=303)


# ---- Share links ---------------------------------------------------------------


def _share_url(request: Request, token: str) -> str:
    scheme = "https" if ON_VERCEL else request.url.scheme
    return f"{scheme}://{request.url.netloc}/s/{token}"


@app.post("/p/{person_id}/share", response_class=HTMLResponse)
def make_share_link(request: Request, person: Person = Depends(get_person), db: Session = Depends(get_db)):
    token = new_share_link(db, person)
    return render(request, "share.html", person=person, link=_share_url(request, token))


@app.post("/p/{person_id}/share/off")
def turn_off_share_link(person: Person = Depends(get_person), db: Session = Depends(get_db)):
    person.share_hash = None
    person.share_created = None
    db.commit()
    return RedirectResponse(f"/p/{person.id}/chart", status_code=303)


# ---- Pages (shared between the owner's view and a share link) ------------------


def _today(request: Request, db: Session, person: Person, base: str, **extra):
    state = request.app.state
    reading = readings.todays_reading(db, person, state.fetch_forecast, state.writer)
    return render(
        request, "today.html", reading=reading, facts=reading.facts, person=person, base=base, tab="today", **extra
    )


def _chart(request: Request, person: Person, base: str, **extra):
    chart = readings.natal_chart(person)
    return render(
        request,
        "chart.html",
        chart=chart,
        wheel=chart_svg(chart),
        person=person,
        born=datetime.fromisoformat(person.birth_local),
        base=base,
        tab="chart",
        **extra,
    )


def _history(request: Request, db: Session, person: Person, base: str, **extra):
    return render(
        request, "history.html", days=readings.history(db, person), person=person, base=base, tab="history", **extra
    )


@app.get("/", response_class=HTMLResponse)
def home(db: Session = Depends(get_db), _: None = Depends(require_login)):
    me = owner(db)
    return RedirectResponse(f"/p/{me.id}" if me else "/people/new", status_code=303)


@app.get("/p/{person_id}", response_class=HTMLResponse)
def person_today(request: Request, person: Person = Depends(get_person), db: Session = Depends(get_db)):
    return _today(request, db, person, f"/p/{person.id}", people=everyone(db))


@app.get("/p/{person_id}/chart", response_class=HTMLResponse)
def person_chart(request: Request, person: Person = Depends(get_person), db: Session = Depends(get_db)):
    return _chart(request, person, f"/p/{person.id}", people=everyone(db))


@app.get("/p/{person_id}/history", response_class=HTMLResponse)
def person_history(request: Request, person: Person = Depends(get_person), db: Session = Depends(get_db)):
    return _history(request, db, person, f"/p/{person.id}", people=everyone(db))


@app.get("/s/{token}", response_class=HTMLResponse)
def shared_today(request: Request, token: str, person: Person = Depends(shared_person), db: Session = Depends(get_db)):
    return _today(request, db, person, f"/s/{token}", shared=True)


@app.get("/s/{token}/chart", response_class=HTMLResponse)
def shared_chart(request: Request, token: str, person: Person = Depends(shared_person)):
    return _chart(request, person, f"/s/{token}", shared=True)


@app.get("/s/{token}/history", response_class=HTMLResponse)
def shared_history(
    request: Request, token: str, person: Person = Depends(shared_person), db: Session = Depends(get_db)
):
    return _history(request, db, person, f"/s/{token}", shared=True)


# Old single-person addresses, in case they're bookmarked.
@app.get("/chart")
@app.get("/history")
@app.get("/setup")
def old_paths(request: Request, db: Session = Depends(get_db), _: None = Depends(require_login)):
    me = owner(db)
    if me is None:
        return RedirectResponse("/people/new", status_code=303)
    suffix = {"/chart": "/chart", "/history": "/history", "/setup": "/edit"}[request.url.path]
    return RedirectResponse(f"/p/{me.id}{suffix}", status_code=303)


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.png", include_in_schema=False)
def favicon() -> FileResponse:
    # Browsers ask for these at the site root regardless of the <link rel="icon"> tag.
    return FileResponse(HERE / "static" / "icon-192.png", media_type="image/png")


@app.get("/healthz")
def healthz() -> Response:
    return Response("ok", media_type="text/plain")
