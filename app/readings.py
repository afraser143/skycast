"""Builds the day's reading once and saves it, so every visit that day shows the same one."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import astro, horoscope
from .db import Person, Reading
from .weather import Forecast

ForecastFetcher = Callable[[float, float, str, date, bool], Forecast | None]


def natal_chart(person: Person) -> astro.Chart:
    return astro.natal_chart(person.birth_utc, person.birth_lat, person.birth_lon)


def local_today(person: Person, now: datetime | None = None) -> date:
    """'Today' where the person lives, so a friend in another time zone gets their own day."""
    now = now or datetime.now(ZoneInfo("UTC"))
    return now.astimezone(ZoneInfo(person.home_tz)).date()


def facts_to_json(facts: horoscope.DayFacts) -> dict:
    return {
        "moon_sign": facts.moon_sign,
        "moon_phase": facts.moon_phase,
        "moon_emoji": facts.moon_emoji,
        "illumination": round(facts.illumination, 3),
        "big_three": facts.big_three,
        "transits": [
            {
                "transiting": t.transiting,
                "aspect": t.aspect,
                "natal": t.natal,
                "orb": t.orb,
                "applying": t.applying,
                "glyph": t.glyph,
            }
            for t in facts.top_transits(5)
        ],
        "retrograde": facts.retrograde,
        "weather": facts.weather.as_dict() if facts.weather else None,
    }


def todays_reading(
    session: Session,
    person: Person,
    fetch_forecast: ForecastFetcher,
    writer: horoscope.Generator | None,
    now: datetime | None = None,
) -> Reading:
    day = local_today(person, now)
    existing = session.get(Reading, (person.id, day))
    if existing:
        return existing

    # Midday local time stands in for "today": the Moon's position at noon is a fair average.
    noon = datetime.combine(day, time(12), tzinfo=ZoneInfo(person.home_tz))
    weather = fetch_forecast(person.home_lat, person.home_lon, person.home_tz, day, person.fahrenheit)
    facts = horoscope.day_facts(natal_chart(person), noon, weather)
    reading, source = horoscope.write_reading(facts, writer)

    row = Reading(
        person_id=person.id,
        day=day,
        focus=reading.focus,
        horoscope=reading.horoscope,
        source=source,
        facts=facts_to_json(facts),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        # Two tabs opened at once and both wrote today's reading: keep the first one.
        session.rollback()
        return session.get(Reading, (person.id, day))
    return row


def history(session: Session, person: Person, limit: int = 60) -> list[Reading]:
    query = select(Reading).where(Reading.person_id == person.id).order_by(Reading.day.desc()).limit(limit)
    return list(session.scalars(query))
