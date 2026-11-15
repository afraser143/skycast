"""Builds the day's reading once and saves it, so every visit that day shows the same one."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import astro, horoscope
from .db import DailyReading, Profile
from .weather import Forecast

ForecastFetcher = Callable[[float, float, str, date, bool], Forecast | None]


def natal_chart(profile: Profile) -> astro.Chart:
    return astro.natal_chart(profile.birth_utc, profile.birth_lat, profile.birth_lon)


def local_today(profile: Profile, now: datetime | None = None) -> date:
    now = now or datetime.now(ZoneInfo("UTC"))
    return now.astimezone(ZoneInfo(profile.home_tz)).date()


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
    profile: Profile,
    fetch_forecast: ForecastFetcher,
    writer: horoscope.Generator | None,
    now: datetime | None = None,
) -> DailyReading:
    day = local_today(profile, now)
    existing = session.get(DailyReading, day)
    if existing:
        return existing

    # Midday local time stands in for "today": the Moon's position at noon is a fair average.
    noon = datetime.combine(day, time(12), tzinfo=ZoneInfo(profile.home_tz))
    weather = fetch_forecast(profile.home_lat, profile.home_lon, profile.home_tz, day, profile.fahrenheit)
    facts = horoscope.day_facts(natal_chart(profile), noon, weather)
    reading, source = horoscope.write_reading(facts, writer)

    row = DailyReading(
        day=day, focus=reading.focus, horoscope=reading.horoscope, source=source, facts=facts_to_json(facts)
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        # Two tabs opened at once and both wrote today's reading: keep the first one.
        session.rollback()
        return session.get(DailyReading, day)
    return row


def history(session: Session, limit: int = 60) -> list[DailyReading]:
    return list(session.scalars(select(DailyReading).order_by(DailyReading.day.desc()).limit(limit)))
