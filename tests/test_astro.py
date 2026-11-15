"""Checks the astrology maths against astronomy, not against itself."""

import math
from datetime import UTC, datetime, timedelta

import astronomy
import pytest

from app import astro

UTC = UTC


def test_sun_is_at_zero_aries_at_the_march_equinox_and_zero_cancer_at_the_june_solstice():
    for year in (1985, 2000, 2026):
        seasons = astronomy.Seasons(year)
        march = seasons.mar_equinox.Utc().replace(tzinfo=UTC)
        june = seasons.jun_solstice.Utc().replace(tzinfo=UTC)
        assert abs(astro.signed_delta(astro.longitude("Sun", astro.to_time(march)), 0)) < 0.01
        assert abs(astro.signed_delta(astro.longitude("Sun", astro.to_time(june)), 90)) < 0.01


def _ecliptic_to_horizon(lon: float, moment: datetime, lat: float, lng: float) -> tuple[float, float]:
    """Altitude/azimuth of a point on the ecliptic, done independently of astro.angles()."""
    t = astro.to_time(moment)
    eps = math.radians(astro.mean_obliquity(t))
    lam = math.radians(lon)
    ra = math.atan2(math.sin(lam) * math.cos(eps), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    ra_hours = (math.degrees(ra) % 360) / 15
    observer = astronomy.Observer(lat, lng, 0)
    hor = astronomy.Horizon(t, observer, ra_hours, math.degrees(dec), astronomy.Refraction.Airless)
    return hor.altitude, hor.azimuth


@pytest.mark.parametrize(
    "moment,lat,lng",
    [
        (datetime(1990, 5, 4, 14, 30, tzinfo=UTC), 30.27, -97.74),  # Austin
        (datetime(2001, 11, 23, 3, 5, tzinfo=UTC), 51.5, -0.13),  # London
        (datetime(1977, 2, 9, 21, 45, tzinfo=UTC), -33.87, 151.21),  # Sydney
        (datetime(2015, 8, 1, 9, 0, tzinfo=UTC), 1.35, 103.82),  # Singapore, near the equator
    ],
)
def test_ascendant_is_rising_on_the_eastern_horizon_and_midheaven_is_on_the_meridian(moment, lat, lng):
    asc, mc = astro.angles(moment, lat, lng)

    alt, az = _ecliptic_to_horizon(asc, moment, lat, lng)
    assert abs(alt) < 0.05, "the Ascendant should sit on the horizon"
    assert 0 < az < 180, "and in the east, not the west"

    # The Midheaven culminates: due south (or north in the southern hemisphere), high in the sky.
    alt_mc, az_mc = _ecliptic_to_horizon(mc, moment, lat, lng)
    assert min(abs(az_mc - 180), abs(az_mc), abs(az_mc - 360)) < 0.05
    assert alt_mc > 0


def test_retrograde_matches_known_mars_retrograde():
    # Mars was retrograde from early December 2024 to late February 2025.
    assert astro.is_retrograde("Mars", datetime(2025, 1, 15, tzinfo=UTC))
    assert not astro.is_retrograde("Mars", datetime(2025, 4, 15, tzinfo=UTC))
    assert not astro.is_retrograde("Sun", datetime(2025, 1, 15, tzinfo=UTC))


def test_signs_and_positions():
    assert astro.sign_of(0) == "Aries"
    assert astro.sign_of(359.99) == "Pisces"
    assert astro.sign_of(-1) == "Pisces"
    assert astro.format_position(44.5) == "14°30′ Taurus"
    assert astro.format_position(29.9999) == "0°00′ Taurus"
    assert astro.format_position(359.9999) == "0°00′ Aries"


def test_whole_sign_houses():
    asc = 155.0  # Virgo rising
    assert astro.whole_sign_house(160.0, asc) == 1  # also Virgo
    assert astro.whole_sign_house(185.0, asc) == 2  # Libra
    assert astro.whole_sign_house(140.0, asc) == 12  # Leo


def test_natal_chart_has_every_planet_and_angles():
    chart = astro.natal_chart(datetime(1990, 5, 4, 14, 30, tzinfo=UTC), 30.27, -97.74)
    assert [p.name for p in chart.placements] == list(astro.PLANETS)
    assert chart.get("Sun").sign == "Taurus"  # early May is always Taurus
    assert chart.ascendant.house == 1
    assert set(chart.big_three) == {"sun", "moon", "rising"}


def _fake_chart(**lons: float) -> astro.Chart:
    placements = [astro.Placement(n, lons.get(n, 999)) for n in astro.PLANETS]
    return astro.Chart(
        placements, astro.Placement("Ascendant", lons.get("Ascendant", 999)), astro.Placement("Midheaven", 999)
    )


def test_transits_find_aspects_to_the_natal_chart():
    moment = datetime(2026, 11, 15, 18, tzinfo=UTC)
    moon_now = astro.longitude("Moon", astro.to_time(moment))
    # Put natal Venus exactly opposite today's Moon, natal Sun square to it.
    chart = _fake_chart(Venus=moon_now + 180, Sun=moon_now + 91)
    found = {(t.transiting, t.aspect, t.natal): t for t in astro.transits(chart, moment)}
    assert ("Moon", "opposition", "Venus") in found
    assert ("Moon", "square", "Sun") in found
    assert found[("Moon", "square", "Sun")].orb == pytest.approx(1.0, abs=0.01)
    # The Moon moves forward ~13°/day, so it is closing in on a point just ahead of the exact square.
    assert found[("Moon", "square", "Sun")].applying


def test_transits_respect_orbs():
    moment = datetime(2026, 11, 15, 18, tzinfo=UTC)
    moon_now = astro.longitude("Moon", astro.to_time(moment))
    chart = _fake_chart(Venus=moon_now + 180 + astro.ORBS["Moon"] + 0.5)
    assert not any(t.natal == "Venus" and t.transiting == "Moon" for t in astro.transits(chart, moment))


def test_moon_phase():
    # Full Moon of 2025-01-13 22:27 UTC (astronomy-engine searches for it).
    full = astronomy.SearchMoonPhase(180, astronomy.Time.Make(2025, 1, 10, 0, 0, 0), 10).Utc().replace(tzinfo=UTC)
    name, _, lit = astro.moon_phase(full)
    assert name == "Full Moon" and lit > 0.99
    name, _, lit = astro.moon_phase(full + timedelta(days=7.4))
    assert name == "Last Quarter"


def test_natal_aspect_lines_and_meanings():
    from app import nature, wheel

    chart = astro.natal_chart(datetime(1990, 5, 4, 14, 30, tzinfo=UTC), 30.27, -97.74)
    for a, b, aspect in wheel.natal_aspects(chart):
        assert aspect != "conjunction"
        assert astro.aspect_between(a.lon, b.lon, wheel.NATAL_ORB)[0] == aspect
    for p in chart.points:
        assert nature.placement_meaning(p.name, p.sign).endswith(".")
    assert [nature.ordinal(n) for n in (1, 2, 3, 4, 11, 12)] == ["1st", "2nd", "3rd", "4th", "11th", "12th"]


def test_planets_sharing_a_sign_get_different_images():
    from app import nature

    # In May 1990, Saturn, Uranus and Neptune were all in Capricorn.
    chart = astro.natal_chart(datetime(1990, 5, 4, 14, 30, tzinfo=UTC), 30.27, -97.74)
    capricorn = [
        nature.placement_meaning(p.name, p.sign).split(": ", 1)[1] for p in chart.placements if p.sign == "Capricorn"
    ]
    assert len(capricorn) == 3 and len(set(capricorn)) == 3
    assert all(len(images) == 3 for images in nature.SIGN_NATURE.values())
