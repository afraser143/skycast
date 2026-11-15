"""The living sky behind each page: the real time of day and weather where the person lives,
and the Moon drawn at its true phase."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import astronomy

from . import astro


@dataclass
class SkyState:
    period: str  # night, dawn, day, dusk
    condition: str  # clear, partly, cloudy, fog, rain, snow, storm
    moon_svg: str

    @property
    def tone(self) -> str:
        """Whether text sits on a dark or a light sky."""
        # Dawn and dusk skies are dark at the top, where the text starts.
        if self.period in ("night", "dawn", "dusk") or self.condition == "storm":
            return "dark"
        return "light"

    @property
    def show_stars(self) -> bool:
        return self.period == "night" and self.condition in ("clear", "partly")


def sun_altitude(moment: datetime, lat: float, lon: float) -> float:
    t = astro.to_time(moment)
    observer = astronomy.Observer(lat, lon, 0)
    eq = astronomy.Equator(astronomy.Body.Sun, t, observer, True, True)
    return astronomy.Horizon(t, observer, eq.ra, eq.dec, astronomy.Refraction.Normal).altitude


def period(moment: datetime, lat: float, lon: float) -> str:
    """Night below -6° (civil twilight), dawn or dusk up to 6°, day above."""
    alt = sun_altitude(moment, lat, lon)
    if alt < -6:
        return "night"
    if alt < 6:
        # Morning or evening twilight: is the Sun climbing?
        later = sun_altitude(moment.astimezone(UTC) + timedelta(minutes=10), lat, lon)
        return "dawn" if later > alt else "dusk"
    return "day"


def moon_svg(moment: datetime, southern: bool = False, size: int = 120) -> str:
    """The Moon at its real phase. In the northern hemisphere a waxing Moon is lit on the right."""
    angle = astronomy.MoonPhase(astro.to_time(moment))  # 0 new, 90 first quarter, 180 full, 270 last quarter
    r = size / 2 - 6
    c = size / 2
    lit_fraction = (1 - math.cos(math.radians(angle))) / 2
    waxing = angle < 180
    lit_on_right = waxing != southern

    # The lit area is bounded by the lit limb (a semicircle) and the terminator (a half-ellipse).
    rx = abs(math.cos(math.radians(angle))) * r
    top, bottom = f"{c:.1f},{c - r:.1f}", f"{c:.1f},{c + r:.1f}"
    limb_sweep = 1 if lit_on_right else 0
    # Gibbous: the terminator bulges into the dark side; crescent: it curves back toward the limb.
    terminator_sweep = limb_sweep if lit_fraction > 0.5 else 1 - limb_sweep
    lit_path = f"M{top} A{r:.1f},{r:.1f} 0 0 {limb_sweep} {bottom} A{rx:.1f},{r:.1f} 0 0 {terminator_sweep} {top} Z"

    label = f"Moon, {round(lit_fraction * 100)}% lit, {'waxing' if waxing else 'waning'}"
    return (
        f'<svg class="moon-svg" viewBox="0 0 {size} {size}" role="img" aria-label="{label}">'
        f'<defs><radialGradient id="moonlit" cx="40%" cy="35%" r="75%">'
        f'<stop offset="0" stop-color="#fffbea"/><stop offset="1" stop-color="#e6dcc0"/></radialGradient></defs>'
        f'<circle cx="{c}" cy="{c}" r="{r + 5:.1f}" class="moon-halo"/>'
        f'<circle cx="{c}" cy="{c}" r="{r:.1f}" class="moon-dark"/>'
        + (f'<path d="{lit_path}" fill="url(#moonlit)"/>' if lit_fraction > 0.01 else "")
        + "</svg>"
    )


def sky_for(moment: datetime, lat: float, lon: float, condition: str) -> SkyState:
    return SkyState(period(moment, lat, lon), condition, moon_svg(moment, southern=lat < 0))
