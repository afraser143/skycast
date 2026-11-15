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

import anthropic
from pydantic import BaseModel, Field

from . import astro
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

    def top_transits(self, n: int = 3) -> list[astro.Transit]:
        return self.transits[:n]

    def for_prompt(self) -> str:
        lines = [
            f"Date: {self.day:%A, %B %-d}",
            f"Natal big three: Sun in {self.big_three['sun']}, Moon in {self.big_three['moon']}, "
            f"{self.big_three['rising']} rising",
            f"Today's Moon: {self.moon_phase} in {self.moon_sign}",
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


def day_facts(chart: astro.Chart, moment: datetime, weather: Forecast | None) -> DayFacts:
    """Everything the horoscope is based on, computed for `moment` (local midday works best)."""
    moon_lon = astro.longitude("Moon", astro.to_time(moment))
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
    )


# ---- Writing -------------------------------------------------------------------


class Reading(BaseModel):
    focus: str = Field(description="A 2-4 word theme for the day, in title case.")
    horoscope: str = Field(description="The 2-3 sentence horoscope.")


SYSTEM_PROMPT = """You write one personal daily horoscope for a single reader, in the second person.

You are given that day's facts: the reader's natal big three, the Moon's sign and phase, the strongest
transits to their natal chart (already calculated), retrograde planets, and their local weather.

Write exactly 2 or 3 sentences, about 40 to 70 words in total:
- Ground the reading in the transits and Moon you were given. Name at most two of them, in plain words.
  Never mention a transit, aspect or placement that isn't in the facts.
- Weave in the weather naturally and practically (for example, a rainy evening as a reason to stay in),
  unless it's unavailable.
- Warm, specific and grounded. No fatalism, no predictions of specific events, and no medical,
  legal or financial advice.

Also give a "focus": a 2-4 word theme for the day, in title case."""

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
    return None


# ---- Fallback ------------------------------------------------------------------

ASPECT_MOOD = {
    "conjunction": "brings {a} and your {b} together",
    "sextile": "gives your {b} an easy opening",
    "trine": "flows smoothly with your {b}",
    "square": "puts some friction on your {b}",
    "opposition": "pulls against your {b}",
}
FOCUS_BY_ASPECT = {
    "conjunction": "Fresh Starts",
    "sextile": "Open Doors",
    "trine": "Easy Flow",
    "square": "Steady Effort",
    "opposition": "Finding Balance",
}


def template_reading(facts: DayFacts) -> Reading:
    """A plain reading built from the facts, used when the AI is off or fails."""
    first = f"With a {facts.moon_phase.lower()} in {facts.moon_sign}"
    top = facts.top_transits(1)
    if top:
        t = top[0]
        mood = ASPECT_MOOD[t.aspect].format(a=t.transiting, b=f"natal {t.natal}")
        sentences = [f"{first}, the {t.transiting} {mood} today."]
        focus = FOCUS_BY_ASPECT[t.aspect]
    else:
        sentences = [f"{first} and few close transits, it's a quiet day in your chart."]
        focus = "Quiet Sky"
    if facts.weather:
        w = facts.weather
        sentences.append(f"Expect {w.summary} with a high of {w.high:.0f}{w.unit}, so plan around it.")
    else:
        sentences.append("Take it at your own pace and notice what draws your attention.")
    return Reading(focus=focus, horoscope=" ".join(sentences))


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
    log.info("[horoscope] ai=on reading used")
    return Reading(focus=reading.focus.strip(), horoscope=reading.horoscope.strip()), "ai"
