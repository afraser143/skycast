"""The natural world's vocabulary: seasons, weather moods, and the curated nature messengers.

Imagery is drawn from the shared language of nature and the elements, not from the
ceremonies or sacred terms of any particular tradition.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

from . import astro

# ---- Seasons -------------------------------------------------------------------

_SEASONS = ["spring", "summer", "autumn", "winter"]


def season(sun_lon: float, lat: float) -> str:
    """'late autumn' from the Sun's ecliptic longitude: astronomical seasons, flipped south of the equator."""
    quarter = int(astro.norm(sun_lon) // 90)  # 0 = March equinox to June solstice
    if lat < 0:
        quarter = (quarter + 2) % 4
    third = int((astro.norm(sun_lon) % 90) // 30)
    return f"{['early', 'mid', 'late'][third]} {_SEASONS[quarter]}"


# ---- Weather -------------------------------------------------------------------

# Open-Meteo WMO codes grouped by how they look and feel.
CONDITIONS = {
    "clear": {0, 1},
    "partly": {2},
    "cloudy": {3},
    "fog": {45, 48},
    "rain": {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82},
    "snow": {71, 73, 75, 77, 85, 86},
    "storm": {95, 96, 99},
}


def condition(code: int | None) -> str:
    if code is None:
        return "clear"
    for name, codes in CONDITIONS.items():
        if code in codes:
            return name
    return "cloudy"


# What each kind of weather invites inwardly (used by the fallback writer).
WEATHER_WISDOM = {
    "clear": "The open sky asks nothing of you but to be seen; let your intentions stand in plain light today.",
    "partly": "Light and shadow trade places across the sky, and you can hold both without choosing just yet.",
    "cloudy": "A quiet grey lid settles over the day, good weather for turning inward and tending what's close.",
    "fog": "Fog softens every edge today, so trust the next few steps rather than the whole road.",
    "rain": "The rain comes to soften hard ground; let it slow your pace and water what you planted.",
    "snow": "Snow hushes the world and asks the same of you: rest is part of growing, not a pause from it.",
    "storm": "Storm energy moves through the air; let it clear out what's stale rather than stir up what's settled.",
}

ASPECT_WISDOM = {
    "conjunction": "something new is taking root",
    "sextile": "a small door opens if you walk toward it",
    "trine": "the current runs with you, so let it carry you",
    "square": "growth comes through friction, the way a seed splits its shell",
    "opposition": "two pulls want your attention, and balance is the work",
}
FOCUS_BY_ASPECT = {
    "conjunction": "New Roots",
    "sextile": "Open Doors",
    "trine": "Following Currents",
    "square": "Through the Shell",
    "opposition": "Finding Balance",
}
FOCUS_BY_CONDITION = {
    "clear": "Open Sky",
    "partly": "Light and Shadow",
    "cloudy": "Quiet Tending",
    "fog": "Next Steps",
    "rain": "Soft Ground",
    "snow": "Deep Rest",
    "storm": "Clearing Winds",
}


# ---- Nature messengers -----------------------------------------------------------


@dataclass(frozen=True)
class Messenger:
    name: str
    kind: str  # creature, bird, plant, tree, stone
    gift: str  # the quality it carries
    meaning: str  # a default line for the fallback writer
    # When the fallback writer should prefer it.
    conditions: frozenset[str] = frozenset()
    aspects: frozenset[str] = frozenset()


def _m(name, kind, gift, meaning, conditions=(), aspects=()):
    return Messenger(name, kind, gift, meaning, frozenset(conditions), frozenset(aspects))


MESSENGERS: list[Messenger] = [
    _m("Heron", "bird", "patience", "Stand still in the shallows; what you need will come to you.", ["rain", "fog"], ["square"]),
    _m("Owl", "bird", "seeing in the dark", "Trust what you notice when things are quiet.", ["fog", "cloudy"], ["opposition"]),
    _m("Hawk", "bird", "perspective", "Rise above the details and look at the whole field.", ["clear", "partly"], ["opposition"]),
    _m("Hummingbird", "bird", "joy", "Drink from what's sweet today without apology.", ["clear"], ["trine", "sextile"]),
    _m("Raven", "bird", "messages", "Pay attention to the words that keep finding you.", ["cloudy", "storm"], ["conjunction"]),
    _m("Wolf", "creature", "instinct and kinship", "Lean on your people, and let them lean on you.", ["snow"], ["opposition"]),
    _m("Fox", "creature", "adaptability", "Change course lightly; there's more than one path through.", ["partly"], ["sextile"]),
    _m("Bear", "creature", "rest and strength", "Retreat to your den and let your strength rebuild.", ["snow", "cloudy"], ["square"]),
    _m("Deer", "creature", "gentleness", "Move softly and stay alert to what feels tender.", ["fog", "rain"], ["trine"]),
    _m("Turtle", "creature", "the long view", "Carry your home with you and take the slow way.", ["rain"], ["square"]),
    _m("Salmon", "creature", "persistence", "Keep swimming upstream toward what calls you home.", ["rain", "storm"], ["square"]),
    _m("Butterfly", "creature", "transformation", "Let the old shape go; something lighter is emerging.", ["clear", "partly"], ["conjunction"]),
    _m("Bee", "creature", "devoted work", "Small, steady visits to what matters make the honey.", ["clear"], ["sextile"]),
    _m("Spider", "creature", "weaving", "Build patiently, one thread connected to the next.", ["fog", "cloudy"], ["sextile"]),
    _m("Moth", "creature", "following the light", "Notice what you're drawn toward after dark.", ["clear"], ["conjunction"]),
    _m("Oak", "tree", "endurance", "Stand rooted; your strength is in what's below the surface.", ["storm"], ["square", "opposition"]),
    _m("Willow", "tree", "flexibility", "Bend with what comes, and you won't break.", ["storm", "rain"], ["opposition"]),
    _m("Cedar", "tree", "protection", "Keep good boundaries and let the air clear.", ["snow", "cloudy"], ["square"]),
    _m("Fern", "plant", "unfurling", "Open slowly; beginnings don't need to be rushed.", ["rain", "fog"], ["conjunction"]),
    _m("Moss", "plant", "quiet persistence", "Soft things grow over hard stones in their own time.", ["rain", "cloudy"], ["trine"]),
    _m("Dandelion", "plant", "resilience", "Grow where you're planted, and send your wishes on the wind.", ["clear", "partly"], ["trine"]),
    _m("Mushroom", "plant", "hidden connection", "Much of what holds you up happens out of sight.", ["rain", "fog"], ["sextile"]),
    _m("River Stone", "stone", "being shaped by time", "Let the current smooth your edges instead of fighting it.", ["rain", "storm"], ["opposition"]),
    _m("Quartz", "stone", "clarity", "Hold one clear intention and let it catch the light.", ["clear", "snow"], ["conjunction"]),
]  # fmt: skip
MESSENGER_NAMES = [m.name for m in MESSENGERS]


def get_messenger(name: str) -> Messenger | None:
    return next((m for m in MESSENGERS if m.name == name), None)


def pick_messenger(cond: str, aspect: str | None, day: date, salt: str = "") -> Messenger:
    """The fallback's choice: messengers that fit both the weather and the strongest aspect first,
    then either one. A hash of the day picks among equals, so it varies but stays put all day."""
    both = [m for m in MESSENGERS if cond in m.conditions and aspect in m.aspects]
    either = [m for m in MESSENGERS if cond in m.conditions or (aspect and aspect in m.aspects)]
    pool = both or either or MESSENGERS
    digest = hashlib.sha256(f"{day.isoformat()}{salt}".encode()).digest()
    return pool[digest[0] % len(pool)]


# ---- Chart meanings ----------------------------------------------------------------

# Each sign's nature, as something in the natural world. Three images per sign, so several
# planets in one sign don't all read the same.
SIGN_NATURE = {
    "Aries": [
        "quick and brave, like the first green shoot of spring",
        "bold and kindling, like a spark catching dry grass",
        "eager and direct, like a ram climbing toward the light",
    ],
    "Taurus": [
        "steady and sensual, rooted like rich soil",
        "patient and loyal, like an old orchard bearing fruit",
        "calm and abundant, like a meadow in late spring",
    ],
    "Gemini": [
        "curious and quick, like wind moving through leaves",
        "bright and restless, like birdsong passing from tree to tree",
        "playful and many-sided, like light on moving water",
    ],
    "Cancer": [
        "tender and protective, like a tide pool sheltering life",
        "deeply feeling, like the sea answering the Moon",
        "nurturing and close, like a nest woven from soft things",
    ],
    "Leo": [
        "warm and radiant, like the high summer sun",
        "generous and proud, like a field of sunflowers",
        "bold-hearted and bright, like firelight gathering others close",
    ],
    "Virgo": [
        "careful and devoted, like a gardener tending each row",
        "quietly skilled, like a bee building perfect comb",
        "clear and grounded, like a harvest sorted with care",
    ],
    "Libra": [
        "graceful and fair, like a breeze finding its balance",
        "harmonious and gentle, like reeds swaying together",
        "drawn to beauty, like evening light resting on still water",
    ],
    "Scorpio": [
        "deep and transforming, like still water over hidden springs",
        "intense and enduring, like roots reaching into the dark",
        "powerful and private, like the earth's slow inner fire",
    ],
    "Sagittarius": [
        "free and far-seeing, like flames leaping toward open sky",
        "adventurous and hopeful, like a trail over the next ridge",
        "wide and searching, like a hawk riding warm air",
    ],
    "Capricorn": [
        "patient and enduring, like a mountain wearing the seasons",
        "steady and ambitious, like a goat climbing sure-footed",
        "disciplined and wise, like a pine holding through winter",
    ],
    "Aquarius": [
        "original and far-sighted, like a high wind clearing the sky",
        "free-thinking and kind, like rain falling on everyone alike",
        "visionary, like the first star appearing at dusk",
    ],
    "Pisces": [
        "dreamy and boundless, like the sea meeting the horizon",
        "compassionate and fluid, like a river finding its way",
        "intuitive and soft, like mist rising from a morning lake",
    ],
}

# What each placement speaks to.
PLACEMENT_THEME = {
    "Sun": "Your core self",
    "Moon": "Your inner tides",
    "Mercury": "How you think and speak",
    "Venus": "How you love and what you value",
    "Mars": "How you act and what you want",
    "Jupiter": "Where you grow and find good fortune",
    "Saturn": "Where you build lasting strength",
    "Uranus": "Where you break new ground",
    "Neptune": "Where you dream and dissolve",
    "Pluto": "Where you transform",
    "Ascendant": "How you meet the world",
    "Midheaven": "Your path and calling",
}


def placement_meaning(name: str, sign: str) -> str:
    """'Sun', 'Taurus' -> 'Your core self: steady and sensual, rooted like rich soil.'
    Neighbouring placements use different images, so three planets in one sign read differently."""
    images = SIGN_NATURE[sign]
    return f"{PLACEMENT_THEME[name]}: {images[list(PLACEMENT_THEME).index(name) % len(images)]}."


def ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
