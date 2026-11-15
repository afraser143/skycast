"""Birth chart and daily transit calculations.

Positions are geocentric longitudes on the true ecliptic of date, which is what
Western (tropical) astrology uses. They come from astronomy-engine, which is
accurate to about an arcminute for the planets, far finer than any sign boundary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import astronomy

SIGNS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]  # fmt: skip
# U+FE0E asks for the text glyph, so iPhones don't swap in colour emoji.
SIGN_GLYPHS = [chr(0x2648 + i) + "︎" for i in range(12)]

PLANETS: dict[str, astronomy.Body] = {
    "Sun": astronomy.Body.Sun,
    "Moon": astronomy.Body.Moon,
    "Mercury": astronomy.Body.Mercury,
    "Venus": astronomy.Body.Venus,
    "Mars": astronomy.Body.Mars,
    "Jupiter": astronomy.Body.Jupiter,
    "Saturn": astronomy.Body.Saturn,
    "Uranus": astronomy.Body.Uranus,
    "Neptune": astronomy.Body.Neptune,
    "Pluto": astronomy.Body.Pluto,
}
GLYPHS = {
    "Sun": "☉", "Moon": "☽", "Mercury": "☿", "Venus": "♀", "Mars": "♂",
    "Jupiter": "♃", "Saturn": "♄", "Uranus": "♅", "Neptune": "♆", "Pluto": "♇",
    "Ascendant": "AC", "Midheaven": "MC",
}  # fmt: skip
GLYPHS = {k: v + "︎" if len(v) == 1 else v for k, v in GLYPHS.items()}


def norm(deg: float) -> float:
    return deg % 360.0


def sign_index(lon: float) -> int:
    return int(norm(lon) // 30)


def sign_of(lon: float) -> str:
    return SIGNS[sign_index(lon)]


def format_position(lon: float) -> str:
    """14°23′ Taurus"""
    # Round to the arcminute first, so 29°59.99′ Aries shows as 0°00′ Taurus, not 30°00′ Aries.
    total = round(norm(lon) * 60) % (360 * 60)
    sign, within = divmod(total, 30 * 60)
    deg, minutes = divmod(within, 60)
    return f"{deg}°{minutes:02d}′ {SIGNS[sign]}"


def to_time(moment: datetime) -> astronomy.Time:
    # A naive datetime is UTC (SQLite drops the zone on stored times); never the server's local time.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    utc = moment.astimezone(UTC)
    return astronomy.Time.Make(utc.year, utc.month, utc.day, utc.hour, utc.minute, utc.second + utc.microsecond / 1e6)


def longitude(name: str, t: astronomy.Time) -> float:
    body = PLANETS[name]
    if body == astronomy.Body.Sun:
        return astronomy.SunPosition(t).elon
    if body == astronomy.Body.Moon:
        return astronomy.EclipticGeoMoon(t).lon
    return astronomy.Ecliptic(astronomy.GeoVector(body, t, True)).elon


def signed_delta(a: float, b: float) -> float:
    """Shortest signed angle from a to b, in (-180, 180]."""
    return (b - a + 540.0) % 360.0 - 180.0


def is_retrograde(name: str, moment: datetime) -> bool:
    if name in ("Sun", "Moon"):
        return False
    before = longitude(name, to_time(moment - timedelta(hours=12)))
    after = longitude(name, to_time(moment + timedelta(hours=12)))
    return signed_delta(before, after) < 0


def mean_obliquity(t: astronomy.Time) -> float:
    """Obliquity of the ecliptic in degrees (IAU 1980). Nutation is omitted;
    it moves the angles below by under 0.01°."""
    c = t.tt / 36525.0
    arcsec = 84381.448 - 46.8150 * c - 0.00059 * c * c + 0.001813 * c**3
    return arcsec / 3600.0


def angles(moment: datetime, lat: float, lon: float) -> tuple[float, float]:
    """Ascendant and Midheaven longitudes for a time and place (east longitude positive)."""
    t = to_time(moment)
    ramc = math.radians(norm(astronomy.SiderealTime(t) * 15.0 + lon))
    eps = math.radians(mean_obliquity(t))
    phi = math.radians(lat)
    mc = math.degrees(math.atan2(math.sin(ramc), math.cos(ramc) * math.cos(eps)))
    asc = math.degrees(math.atan2(math.cos(ramc), -(math.sin(ramc) * math.cos(eps) + math.tan(phi) * math.sin(eps))))
    return norm(asc), norm(mc)


@dataclass
class Placement:
    name: str
    lon: float
    retrograde: bool = False
    house: int | None = None

    @property
    def sign(self) -> str:
        return sign_of(self.lon)

    @property
    def position(self) -> str:
        return format_position(self.lon)

    @property
    def glyph(self) -> str:
        return GLYPHS[self.name]


@dataclass
class Chart:
    placements: list[Placement]
    ascendant: Placement
    midheaven: Placement

    def get(self, name: str) -> Placement:
        for p in self.points:
            if p.name == name:
                return p
        raise KeyError(name)

    @property
    def points(self) -> list[Placement]:
        """Everything transits can aspect: planets plus the two angles."""
        return [*self.placements, self.ascendant, self.midheaven]

    @property
    def big_three(self) -> dict[str, str]:
        return {"sun": self.get("Sun").sign, "moon": self.get("Moon").sign, "rising": self.ascendant.sign}


def whole_sign_house(lon: float, asc: float) -> int:
    """Whole-sign houses: the rising sign is the 1st house, the next sign the 2nd, and so on."""
    return (sign_index(lon) - sign_index(asc)) % 12 + 1


def natal_chart(birth_utc: datetime, lat: float, lon: float) -> Chart:
    t = to_time(birth_utc)
    asc, mc = angles(birth_utc, lat, lon)
    placements = [
        Placement(name, longitude(name, t), is_retrograde(name, birth_utc), whole_sign_house(longitude(name, t), asc))
        for name in PLANETS
    ]
    return Chart(
        placements,
        Placement("Ascendant", asc, house=1),
        Placement("Midheaven", mc, house=whole_sign_house(mc, asc)),
    )


def sky(moment: datetime) -> list[Placement]:
    """Where the planets are at a moment (no angles: those depend on a place)."""
    t = to_time(moment)
    return [Placement(name, longitude(name, t), is_retrograde(name, moment)) for name in PLANETS]


# ---- Aspects -------------------------------------------------------------------

ASPECTS = {"conjunction": 0, "sextile": 60, "square": 90, "trine": 120, "opposition": 180}
ASPECT_GLYPHS = {"conjunction": "☌", "sextile": "⚹", "square": "□", "trine": "△", "opposition": "☍"}

# How close (in degrees) a transit must be to count today. The Moon moves ~13°/day,
# so a wider orb keeps its aspects to roughly "today"; slow planets get tight orbs.
ORBS = {
    "Moon": 5.0, "Sun": 3.0, "Mercury": 3.0, "Venus": 3.0, "Mars": 3.0,
    "Jupiter": 2.0, "Saturn": 2.0, "Uranus": 1.5, "Neptune": 1.5, "Pluto": 1.5,
}  # fmt: skip
# How much a transiting planet colours a single day.
DAILY_WEIGHT = {
    "Moon": 3.0, "Sun": 2.5, "Mercury": 2.0, "Venus": 2.0, "Mars": 2.0,
    "Jupiter": 1.5, "Saturn": 1.5, "Uranus": 1.0, "Neptune": 1.0, "Pluto": 1.0,
}  # fmt: skip
# Natal points that feel most personal.
PERSONAL = {"Sun": 1.5, "Moon": 1.5, "Ascendant": 1.4, "Venus": 1.2, "Mercury": 1.1, "Mars": 1.1}


@dataclass
class Transit:
    transiting: str
    aspect: str
    natal: str
    orb: float
    applying: bool
    score: float = field(default=0.0, compare=False)

    def describe(self) -> str:
        motion = "applying" if self.applying else "separating"
        return f"transiting {self.transiting} {self.aspect} natal {self.natal} ({self.orb:.1f}° orb, {motion})"

    @property
    def glyph(self) -> str:
        return f"{GLYPHS[self.transiting]} {ASPECT_GLYPHS[self.aspect]}︎ {GLYPHS[self.natal]}"


def aspect_between(a: float, b: float, max_orb: float) -> tuple[str, float] | None:
    sep = abs(signed_delta(a, b))
    best = None
    for name, angle in ASPECTS.items():
        orb = abs(sep - angle)
        if orb <= max_orb and (best is None or orb < best[1]):
            best = (name, orb)
    return best


def transits(chart: Chart, moment: datetime) -> list[Transit]:
    """Aspects from today's planets to the natal chart, strongest first."""
    now = {p.name: p.lon for p in sky(moment)}
    soon = {p.name: p.lon for p in sky(moment + timedelta(hours=2))}
    found: list[Transit] = []
    for t_name, t_lon in now.items():
        for natal in chart.points:
            hit = aspect_between(t_lon, natal.lon, ORBS[t_name])
            if not hit:
                continue
            aspect, orb = hit
            later = abs(abs(signed_delta(soon[t_name], natal.lon)) - ASPECTS[aspect])
            score = DAILY_WEIGHT[t_name] * PERSONAL.get(natal.name, 1.0) * (1.0 - orb / ORBS[t_name] * 0.5)
            found.append(Transit(t_name, aspect, natal.name, round(orb, 2), later < orb, round(score, 3)))
    return sorted(found, key=lambda t: t.score, reverse=True)


# ---- Moon phase ----------------------------------------------------------------

PHASES = [
    "New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous",
    "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent",
]  # fmt: skip
PHASE_EMOJI = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]


def moon_phase(moment: datetime) -> tuple[str, str, float]:
    """Phase name, emoji, and illuminated fraction (0–1)."""
    angle = astronomy.MoonPhase(to_time(moment))
    index = int(((angle + 22.5) % 360) // 45)
    illumination = (1 - math.cos(math.radians(angle))) / 2
    return PHASES[index], PHASE_EMOJI[index], illumination
