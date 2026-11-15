"""Turns today's computed sky and forecast into a short horoscope.

The astrology is calculated in astro.py; Claude only writes the prose, and is
told to use only the transits it is given. Only chart placements are sent:
never the birth date, time or place.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from . import astro, nature
from .weather import Forecast

log = logging.getLogger(__name__)


@dataclass
class DayFacts:
    day: date
    big_three: dict[str, str]
    moon_sign: str
    moon_phase: str
    moon_emoji: str
    illumination: float
    transits: list[astro.Transit]
    retrograde: list[str]
    weather: Forecast | None
    season: str = ""

    @property
    def condition(self) -> str:
        return nature.condition(self.weather.code if self.weather else None)

    def top_transits(self, n: int = 3) -> list[astro.Transit]:
        return self.transits[:n]

    def for_prompt(self) -> str:
        lines = [
            f"Date: {self.day:%A, %B %-d}",
            f"Natal big three: Sun in {self.big_three['sun']}, Moon in {self.big_three['moon']}, "
            f"{self.big_three['rising']} rising",
            f"Today's Moon: {self.moon_phase} in {self.moon_sign}",
            f"Season where the reader lives: {self.season}",
        ]
        top = self.top_transits()
        if top:
            lines.append("Strongest transits to the natal chart today:")
            lines += [f"- {t.describe()}" for t in top]
        else:
            lines.append("No close transits to the natal chart today: a quiet sky.")
        if self.retrograde:
            lines.append(f"Retrograde today: {', '.join(self.retrograde)}")
        lines.append(f"Local weather: {self.weather.for_prompt()}" if self.weather else "Local weather: unavailable")
        return "\n".join(lines)


def day_facts(chart: astro.Chart, moment: datetime, weather: Forecast | None, lat: float = 45.0) -> DayFacts:
    """Everything the horoscope is based on, computed for `moment` (local midday works best).
    `lat` is where the person lives, which decides the season."""
    t = astro.to_time(moment)
    moon_lon = astro.longitude("Moon", t)
    phase, emoji, lit = astro.moon_phase(moment)
    return DayFacts(
        day=moment.date(),
        big_three=chart.big_three,
        moon_sign=astro.sign_of(moon_lon),
        moon_phase=phase,
        moon_emoji=emoji,
        illumination=lit,
        transits=astro.transits(chart, moment),
        retrograde=[p.name for p in astro.sky(moment) if p.retrograde],
        weather=weather,
        season=nature.season(astro.longitude("Sun", t), lat),
    )


# ---- Writing -------------------------------------------------------------------


MessengerName = Literal[tuple(nature.MESSENGER_NAMES)]  # type: ignore[valid-type]


class Reading(BaseModel):
    focus: str = Field(description="A 2-4 word theme for the day, in title case.")
    horoscope: str = Field(description="The 2-3 sentence reading.")
    messenger: MessengerName = Field(description="The day's nature messenger, from the allowed list.")
    messenger_message: str = Field(description="One short sentence: what the messenger asks of the reader today.")


_MESSENGER_LIST = "\n".join(f"- {m.name} ({m.kind}): {m.gift}" for m in nature.MESSENGERS)

SYSTEM_PROMPT = f"""You are an earth-wise guide who reads the land and the sky, writing one person's daily reading
in the second person. You believe the outer world mirrors the inner one: the weather, the season and the Moon
all carry meaning for the reader's spirit, relationships, work and energy.

You are given that day's facts: the reader's natal big three, the Moon's sign and phase, the season where they
live, the strongest transits to their natal chart (already calculated), retrograde planets, and their weather.

Write the reading in exactly 2 or 3 sentences, about 40 to 70 words in total:
- Begin with something the reader can see or feel outside today (the rain, the wind, the light, the season,
  the Moon) and draw its meaning inward. If the weather is unavailable, begin with the season or the Moon.
- Weave in at most two of the given transits in plain, natural language, for example "the Moon pressing
  against your Midheaven". Never mention a transit, aspect or placement that isn't in the facts.
- Use grounded images from nature: roots, seeds, water, stone, wind, light, seasons, tides.
- Be warm, quietly wise and specific. Avoid clichés like "the universe has a plan". Don't predict specific
  events, and give no medical, legal or financial advice.
- Draw only on the shared language of nature and the elements. Don't use terms or practices from particular
  spiritual or Indigenous traditions (no "spirit animal", "shaman", "medicine wheel" or similar).

Also give:
- "focus": a 2-4 word theme for the day, in title case.
- "messenger": the one nature messenger from this list whose nature best mirrors the day:
{_MESSENGER_LIST}
- "messenger_message": one short sentence (under 20 words) on what that messenger asks of the reader today."""

Generator = Callable[[DayFacts], Reading | None]
_client: anthropic.Anthropic | None = None


def claude_writer(facts: DayFacts) -> Reading | None:
    """Asks Claude for the reading. Returns None on any failure so the caller can fall back."""
    global _client
    _client = _client or anthropic.Anthropic(timeout=30.0, max_retries=1)
    try:
        response = _client.beta.messages.parse(
            model="claude-opus-5-5",
            max_tokens=4000,
            # If the request is declined, the API retries it on a fallback model in the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            output_format=Reading,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": facts.for_prompt()}],
        )
    except anthropic.APIError as err:
        log.warning("[horoscope] Claude call failed: %s", err)
        return None
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        log.warning("[horoscope] Claude stopped early: stop_reason=%s", response.stop_reason)
        return None
    return response.parsed_output


def ai_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def sentence_count(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


def reading_issue(reading: Reading) -> str | None:
    """Why a generated reading can't be used, or None if it's fine."""
    text = reading.horoscope.strip()
    n = sentence_count(text)
    if not 2 <= n <= 3:
        return f"{n} sentences"
    if len(text) > 600:
        return f"too long ({len(text)} chars)"
    if not 1 <= len(reading.focus.split()) <= 5:
        return "focus isn't 2-4 words"
    if nature.get_messenger(reading.messenger) is None:
        return f"unknown messenger {reading.messenger!r}"
    message = reading.messenger_message.strip()
    if not 3 <= len(message) <= 160 or sentence_count(message) != 1:
        return "messenger message isn't one short sentence"
    return None


# ---- Fallback ------------------------------------------------------------------


def template_reading(facts: DayFacts) -> Reading:
    """A reading built from the facts in the same earth-wise voice, used when the AI is off or fails."""
    cond = facts.condition
    top = facts.top_transits(1)
    aspect = top[0].aspect if top else None

    if facts.weather:
        opening = nature.WEATHER_WISDOM[cond]
    else:
        opening = (
            f"It's {facts.season}, and the {facts.moon_phase.lower()} in {facts.moon_sign} "
            "asks you to move with the season rather than against it."
        )
    if top:
        t = top[0]
        middle = f"With the {t.transiting} {_ASPECT_VERB[t.aspect]} your {t.natal}, {nature.ASPECT_WISDOM[t.aspect]}."
    else:
        middle = "Your chart is quiet today, a still pond; let it settle and see what rises to the surface."

    messenger = nature.pick_messenger(cond, aspect, facts.day, salt="|".join(facts.big_three.values()))
    return Reading(
        focus=_fallback_focus(cond, aspect, has_weather=facts.weather is not None),
        horoscope=f"{opening} {middle}",
        messenger=messenger.name,
        messenger_message=messenger.meaning,
    )


def _fallback_focus(cond: str, aspect: str | None, has_weather: bool) -> str:
    """Name the day after the weather when there is some, else the strongest transit, else stillness."""
    if has_weather:
        return nature.FOCUS_BY_CONDITION[cond]
    return nature.FOCUS_BY_ASPECT[aspect] if aspect else "Quiet Waters"


_ASPECT_VERB = {
    "conjunction": "meeting",
    "sextile": "nudging",
    "trine": "flowing with",
    "square": "pressing against",
    "opposition": "pulling across from",
}


def write_reading(facts: DayFacts, writer: Generator | None) -> tuple[Reading, str]:
    """The day's reading and where it came from ("ai" or "template"). Logs one line either way."""
    if writer is None:
        log.info("[horoscope] ai=off (ANTHROPIC_API_KEY not set): using template")
        return template_reading(facts), "template"
    reading = writer(facts)
    if reading is None:
        log.info("[horoscope] ai=on but no reading returned: using template")
        return template_reading(facts), "template"
    issue = reading_issue(reading)
    if issue:
        log.info("[horoscope] ai=on but reading rejected (%s): using template", issue)
        return template_reading(facts), "template"
    log.info("[horoscope] ai=on reading used (messenger: %s)", reading.messenger)
    return (
        Reading(
            focus=reading.focus.strip(),
            horoscope=reading.horoscope.strip(),
            messenger=reading.messenger,
            messenger_message=reading.messenger_message.strip(),
        ),
        "ai",
    )
