"""Wikipedia team-season infobox -> OC/DC coordinator rows.

Pro Football Reference returns HTTP 403 to automated fetches and is not usable at
all. Wikipedia's `{{Infobox NFL team season}}` / `{{Infobox gridiron football team
season}}` templates are the only fetchable source this library found, and even
there `off_coach`/`def_coach` are populated on only ~37% of sampled team-seasons
(see ffl_bigquery/coordinators/schema.py's `name` column gotcha for the
measurement).

Two parsing subtleties are worth documenting up front:

1. **A field can be present but blank.** `| def_coach       = ` immediately
   followed by the next infobox key, with nothing between them. Infobox values
   legitimately span multiple lines in general (e.g. `pro_bowlers` holds a
   `{{Collapsible list ...}}` template across several lines), so
   `extract_infobox_field` captures up to the *next* `| key =` line rather than
   stopping at the first newline. That is exactly what makes a blank field bleed
   into the next key's whole line when nothing terminates the (empty) value first:
   on the real 2019 Patriots page, asking for `def_coach` extracts the raw string
   `"| owner           = [[Robert Kraft]]"`, because the greedy whitespace after
   `=` swallows straight through the blank line to the very next non-whitespace
   character, and the "capture to next field boundary" then consumes that whole
   next line. `looks_like_bleed` rejects any cleaned value containing `|` or `=`
   -- both are wikitext syntax that can never legitimately appear in a person's
   name -- so a bled value never reaches the table as a name; it produces no row
   at all, which is what a truly blank field should do.
2. `[[wiki links]]` (piped or not) and `{{small|...}}` annotation templates (used
   for things like "(interim)") are stripped so the stored name is plain text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from ffl_bigquery.http import ThrottledSession

WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"

# role -> the infobox field Wikipedia's team-season templates use for it.
ROLE_FIELDS: dict[str, str] = {"OC": "off_coach", "DC": "def_coach"}

# Current (2026) franchise name for each nflverse team abbreviation, as it
# appears in that team's Wikipedia "<season> <Franchise Name> season" page
# title. Pre-relocation/rename seasons for LV/LAC/LAR/WAS will 404 against this
# mapping -- see the `team` column gotcha in coordinators/schema.py.
TEAM_WIKI_NAMES: dict[str, str] = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys", "DEN": "Denver Broncos",
    "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars", "KC": "Kansas City Chiefs",
    "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers",
    "LAR": "Los Angeles Rams", "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings", "NE": "New England Patriots",
    "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers", "SF": "San Francisco 49ers",
    "SEA": "Seattle Seahawks", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}

_SMALL_TEMPLATE_RE = re.compile(r"\{\{small\|[^{}]*\}\}", re.IGNORECASE)
_WIKILINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
_SINGLE_WIKILINK_RE = re.compile(r"^\[\[[^\[\]]+\]\]$")


@dataclass(frozen=True)
class WikipediaPage:
    title: str
    wikitext: str


def page_title(season: int, team: str) -> str:
    """The Wikipedia page title for a team's season, e.g. '2024_Green_Bay_Packers_season'."""
    name = TEAM_WIKI_NAMES[team]
    return f"{season}_{name.replace(' ', '_')}_season"


def fetch_season_wikitext(
    session: ThrottledSession, *, page_title: str
) -> WikipediaPage | None:
    """Fetch section 0 (the infobox) of a team-season page.

    Returns None -- never raises -- when the page doesn't exist: Wikipedia's
    action=parse API responds HTTP 200 with an `error` object (no `parse` key)
    rather than a 404, so this is a normal, exception-free JSON branch.
    """
    payload = session.get_json(
        WIKIPEDIA_API_URL,
        {
            "action": "parse", "page": page_title, "prop": "wikitext",
            "section": 0, "format": "json", "formatversion": 2,
        },
    )
    parse = payload.get("parse")
    if not parse or "wikitext" not in parse:
        return None
    return WikipediaPage(title=parse.get("title", page_title), wikitext=parse["wikitext"])


def extract_infobox_field(wikitext: str, field: str) -> str | None:
    """Raw (unstripped) value of an infobox `| field = ...` line.

    None if the field key doesn't appear in the infobox at all -- distinct from
    an empty-but-present value. Captures up to the next `| key =` line (or the
    closing `}}`) rather than the first newline, because some infobox values
    legitimately span multiple lines. Callers MUST run the result through
    `looks_like_bleed` before trusting it as a value -- see the module
    docstring for why a blank field can otherwise bleed into the next key.
    """
    pattern = re.compile(
        r"\|\s*" + re.escape(field) + r"\s*=\s*(.*?)\n\s*(?=\||\}\})",
        re.DOTALL,
    )
    m = pattern.search(wikitext)
    if not m:
        return None
    return m.group(1)


def strip_wiki_markup(raw: str) -> str:
    """Strip {{small|...}} templates and reduce [[links]] / [[target|text]] to
    their display text."""
    text = _SMALL_TEMPLATE_RE.sub("", raw)
    text = _WIKILINK_RE.sub(r"\1", text)
    return text.strip()


def looks_like_bleed(cleaned: str) -> bool:
    """A cleaned value containing wikitext syntax is not a name -- it's a
    stray infobox line captured because the requested field was blank (see
    module docstring)."""
    return "|" in cleaned or "=" in cleaned


def parse_coordinators(
    wikitext: str, *, season: int, team: str, source: str, retrieved_at: datetime,
) -> list[dict]:
    """OC/DC rows for one team-season's infobox wikitext. Never raises; a
    missing, blank, or bled field simply contributes no row for that role."""
    rows: list[dict] = []
    for role, field in ROLE_FIELDS.items():
        raw = extract_infobox_field(wikitext, field)
        if raw is None:
            continue
        cleaned = strip_wiki_markup(raw)
        if not cleaned:
            continue
        if looks_like_bleed(cleaned):
            continue
        confidence = 1.0 if _SINGLE_WIKILINK_RE.match(raw.strip()) else 0.75
        rows.append({
            "season": season, "team": team, "role": role, "name": cleaned,
            "source": source, "confidence": confidence, "retrieved_at": retrieved_at,
        })
    return rows
