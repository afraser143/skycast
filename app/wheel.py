"""Draws the birth chart wheel as an inline SVG, in fine glowing lines.

Astrological convention: the Ascendant sits at 9 o'clock and the zodiac runs
counter-clockwise from it, so houses 1-6 are below the horizon line.
"""

from __future__ import annotations

import math
from html import escape

from . import astro

SIZE = 360
C = SIZE / 2
R_OUTER = 170
R_SIGNS = 140  # inner edge of the sign band
R_PLANETS = 113
R_INNER = 84  # natal aspect lines are drawn inside this circle
R_HOUSES = R_INNER + 9  # a band of its own, between the aspect lines and the planets

NATAL_ORB = 6.0
HARMONIOUS = {"trine", "sextile"}
TENSE = {"square", "opposition"}


def _screen_angle(lon: float, asc: float) -> float:
    """Ecliptic longitude -> SVG angle in radians (Ascendant on the left, counter-clockwise)."""
    return math.radians(180.0 - (lon - asc))


def _point(lon: float, asc: float, r: float) -> tuple[float, float]:
    a = _screen_angle(lon, asc)
    # SVG y grows downward, so +sin puts later signs below the horizon (counter-clockwise on screen).
    return C + r * math.cos(a), C + r * math.sin(a)


def _spread(lons: list[float], min_gap: float = 10.0) -> list[float]:
    """Nudges planets apart so clustered glyphs don't overlap (positions shown are display-only)."""
    order = sorted(range(len(lons)), key=lambda i: lons[i])
    shown = [lons[i] for i in order]
    for _ in range(12):
        moved = False
        for k in range(1, len(shown)):
            gap = shown[k] - shown[k - 1]
            if gap < min_gap:
                shift = (min_gap - gap) / 2
                shown[k - 1] -= shift
                shown[k] += shift
                moved = True
        if not moved:
            break
    out = [0.0] * len(lons)
    for pos, i in enumerate(order):
        out[i] = shown[pos]
    return out


def natal_aspects(chart: astro.Chart) -> list[tuple[astro.Placement, astro.Placement, str]]:
    """Major aspects between the natal planets (not the angles), within a 6° orb."""
    found = []
    planets = chart.placements
    for i, a in enumerate(planets):
        for b in planets[i + 1 :]:
            hit = astro.aspect_between(a.lon, b.lon, NATAL_ORB)
            if hit and hit[0] != "conjunction":  # a conjunction would be a line of zero length
                found.append((a, b, hit[0]))
    return found


def _line(x1, y1, x2, y2, cls) -> str:
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" class="{cls}"/>'


def chart_svg(chart: astro.Chart) -> str:
    asc = chart.ascendant.lon
    parts = [
        f'<svg viewBox="0 0 {SIZE} {SIZE}" role="img" aria-label="Birth chart wheel" class="wheel">',
        '<defs><radialGradient id="wheel-core"><stop offset="0" stop-color="var(--gold)" stop-opacity="0.16"/>'
        '<stop offset="1" stop-color="var(--gold)" stop-opacity="0"/></radialGradient></defs>',
        f'<circle cx="{C}" cy="{C}" r="{R_INNER}" fill="url(#wheel-core)"/>',
    ]

    # Rings.
    for r, cls in ((R_OUTER, "ring ring-outer"), (R_SIGNS, "ring"), (R_INNER, "ring ring-faint")):
        parts.append(f'<circle cx="{C}" cy="{C}" r="{r}" class="{cls}"/>')

    # Degree ticks around the outside edge: every 5°, longer every 10°.
    for d in range(0, 360, 5):
        length = 6 if d % 10 == 0 else 3
        parts.append(_line(*_point(d, asc, R_OUTER), *_point(d, asc, R_OUTER - length), "degree"))

    # Signs: dividers through the band, and a gold glyph in each.
    for i in range(12):
        parts.append(_line(*_point(i * 30, asc, R_OUTER), *_point(i * 30, asc, R_SIGNS), "sign-divider"))
        gx, gy = _point(i * 30 + 15, asc, (R_OUTER + R_SIGNS) / 2 - 1)
        parts.append(
            f'<text x="{gx:.1f}" y="{gy:.1f}" class="sign-glyph"><title>{astro.SIGNS[i]}</title>'
            f"{astro.SIGN_GLYPHS[i]}</text>"
        )

    # Whole-sign houses: each house is one sign, numbered from the rising sign.
    first = astro.sign_index(asc)
    for house in range(1, 13):
        sign = (first + house - 1) % 12
        parts.append(_line(*_point(sign * 30, asc, R_SIGNS), *_point(sign * 30, asc, R_INNER), "cusp"))
        hx, hy = _point(sign * 30 + 15, asc, R_HOUSES)
        parts.append(f'<text x="{hx:.1f}" y="{hy:.1f}" class="house-number">{house}</text>')

    # Natal aspects: a quiet constellation between the planets.
    for a, b, aspect in natal_aspects(chart):
        cls = "aspect aspect-easy" if aspect in HARMONIOUS else "aspect aspect-tense"
        parts.append(_line(*_point(a.lon, asc, R_INNER), *_point(b.lon, asc, R_INNER), cls))
    for p in chart.placements:
        x, y = _point(p.lon, asc, R_INNER)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.6" class="aspect-node"/>')

    # Horizon (Ascendant-Descendant) and meridian (MC-IC) axes.
    for lon, label in ((asc, "AC"), (chart.midheaven.lon, "MC")):
        parts.append(_line(*_point(lon, asc, R_OUTER + 4), *_point(lon, asc, R_SIGNS), "axis"))
        parts.append(_line(*_point(lon + 180, asc, R_OUTER + 4), *_point(lon + 180, asc, R_SIGNS), "axis"))
        # Just outside the rim, nudged off the line toward the centre of the page, so it never clips.
        lx, ly = _point(lon, asc, R_OUTER + 4)
        lx += 12 if lx < C else -12
        ly -= 9
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" class="axis-label">{label}</text>')

    # Planets: a tick at the true position, the glyph nudged clear of its neighbours.
    shown = _spread([p.lon for p in chart.placements])
    for p, display in zip(chart.placements, shown, strict=True):
        title = escape(f"{p.name} {p.position}{' (retrograde)' if p.retrograde else ''}")
        parts.append(_line(*_point(p.lon, asc, R_SIGNS), *_point(p.lon, asc, R_SIGNS - 6), "tick"))
        gx, gy = _point(display, asc, R_PLANETS)
        parts.append(f'<text x="{gx:.1f}" y="{gy:.1f}" class="planet-glyph"><title>{title}</title>{p.glyph}</text>')

    parts.append("</svg>")
    return "".join(parts)
