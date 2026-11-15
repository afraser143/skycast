"""Draws the birth chart wheel as an inline SVG.

Astrological convention: the Ascendant sits at 9 o'clock and the zodiac runs
counter-clockwise from it, so houses 1-6 are below the horizon line.
"""

from __future__ import annotations

import math
from html import escape

from . import astro

SIZE = 360
C = SIZE / 2
R_OUTER = 172
R_SIGNS = 144  # inner edge of the sign ring
R_PLANETS = 118
R_INNER = 70

ELEMENT_FILL = ["var(--fire)", "var(--earth)", "var(--air)", "var(--water)"]


def _screen_angle(lon: float, asc: float) -> float:
    """Ecliptic longitude -> SVG angle in radians (Ascendant on the left, counter-clockwise)."""
    return math.radians(180.0 - (lon - asc))


def _point(lon: float, asc: float, r: float) -> tuple[float, float]:
    a = _screen_angle(lon, asc)
    # SVG y grows downward, so +sin puts later signs below the horizon (counter-clockwise on screen).
    return C + r * math.cos(a), C + r * math.sin(a)


def _spread(lons: list[float], min_gap: float = 8.0) -> list[float]:
    """Nudges planets apart so clustered glyphs don't overlap (positions shown are display-only)."""
    order = sorted(range(len(lons)), key=lambda i: lons[i])
    shown = [lons[i] for i in order]
    for _ in range(10):
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


def chart_svg(chart: astro.Chart) -> str:
    asc = chart.ascendant.lon
    parts = [
        f'<svg viewBox="0 0 {SIZE} {SIZE}" role="img" aria-label="Birth chart wheel" class="wheel">',
        f'<circle cx="{C}" cy="{C}" r="{R_OUTER}" class="wheel-bg"/>',
    ]

    # Sign ring, tinted by element.
    for i in range(12):
        start, end = i * 30, (i + 1) * 30
        x1, y1 = _point(start, asc, R_OUTER)
        x2, y2 = _point(end, asc, R_OUTER)
        x3, y3 = _point(end, asc, R_SIGNS)
        x4, y4 = _point(start, asc, R_SIGNS)
        parts.append(
            f'<path d="M{x1:.1f},{y1:.1f} A{R_OUTER},{R_OUTER} 0 0 0 {x2:.1f},{y2:.1f} '
            f'L{x3:.1f},{y3:.1f} A{R_SIGNS},{R_SIGNS} 0 0 1 {x4:.1f},{y4:.1f} Z" '
            f'style="fill:{ELEMENT_FILL[i % 4]}" class="sign-slice"/>'
        )
        gx, gy = _point(start + 15, asc, (R_OUTER + R_SIGNS) / 2)
        parts.append(
            f'<text x="{gx:.1f}" y="{gy:.1f}" class="sign-glyph"><title>{astro.SIGNS[i]}</title>'
            f"{astro.SIGN_GLYPHS[i]}</text>"
        )

    # Whole-sign house cusps are the sign boundaries; draw them through the inner ring.
    for i in range(12):
        x1, y1 = _point(i * 30, asc, R_SIGNS)
        x2, y2 = _point(i * 30, asc, R_INNER)
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" class="cusp"/>')
    parts.append(f'<circle cx="{C}" cy="{C}" r="{R_SIGNS}" class="ring"/>')
    parts.append(f'<circle cx="{C}" cy="{C}" r="{R_INNER}" class="ring"/>')

    # Horizon (Ascendant-Descendant) and meridian (MC-IC) axes.
    for lon, label in ((asc, "AC"), (chart.midheaven.lon, "MC")):
        x1, y1 = _point(lon, asc, R_SIGNS)
        x2, y2 = _point(lon + 180, asc, R_SIGNS)
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" class="axis"/>')
        # Inside the planet ring, so a planet sitting on an angle never covers its label.
        lx, ly = _point(lon, asc, R_INNER + 12)
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" class="axis-label">{label}</text>')

    # Planets: a tick at the true position on the inner ring, the glyph nudged clear of neighbours.
    shown = _spread([p.lon for p in chart.placements])
    for p, display in zip(chart.placements, shown, strict=True):
        tx1, ty1 = _point(p.lon, asc, R_SIGNS)
        tx2, ty2 = _point(p.lon, asc, R_SIGNS - 7)
        gx, gy = _point(display, asc, R_PLANETS)
        title = escape(f"{p.name} {p.position}{' (retrograde)' if p.retrograde else ''}")
        parts.append(f'<line x1="{tx1:.1f}" y1="{ty1:.1f}" x2="{tx2:.1f}" y2="{ty2:.1f}" class="tick"/>')
        parts.append(f'<text x="{gx:.1f}" y="{gy:.1f}" class="planet-glyph"><title>{title}</title>{p.glyph}</text>')

    parts.append("</svg>")
    return "".join(parts)
