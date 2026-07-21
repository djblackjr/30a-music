"""
app/dashboard/render.py
Dumb dashboard renderer.

Reads canonical events + their observations from the database and fills
app/dashboard/template.html. It ONLY renders precomputed values — it never
computes confidence, reconciliation, venue defaults, or canonical names.

The template is the hand-built design (corridor map, Today card, Google Maps
directions modal, sortable/responsive table); this module adds the intelligence
columns (Sources, Confidence), the expandable observation detail, and the health
metrics — nothing else.
"""
import csv
import html
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

from app.database.db import (
    DB_PATH,
    get_connection,
    load_current_events,
    load_event_observations,
    load_events,
)
from app.normalize.confidence import confidence_band

logger = logging.getLogger(__name__)

TEMPLATE = Path("app/dashboard/template.html")
DEFAULT_OUT = Path("docs/index.html")

# Editable favorite-venues roster, maintained by hand as a flat CSV (same
# "flat file as source of truth" pattern as venues.txt/artists.txt) rather
# than a code-edited table. One venue name per line under a `venue` header —
# presence in the file means favorite, there's no Y/N column. Unlisted
# venues default to not-favorite — see _venue_favorite().
VENUE_FAVORITES_CSV = Path("app/dashboard/venue_groups.csv")

# Editable performer favorites, same flat-CSV pattern as VENUE_FAVORITES_CSV.
# Unlisted performers default to N.
ARTISTS_CSV = Path("app/dashboard/artists.csv")

# Venues with a dedicated colour in the template's .vt-* classes and map legend.
# Keyed by every spelling variant seen across sources; anything else is vt-def.
VENUE_CLASS = {
    "red fish taco": "vt-rft",
    "papa surf": "vt-ps",
    "papa surf burger bar": "vt-ps",
    "shelby's beach bar and gill": "vt-sbb",
    "shelby's beach bar": "vt-sbb",
    "shelby's": "vt-sbb",
    "north beach social": "vt-nbs",
    "stinky's bait shack": "vt-sbs",
    "stinky’s bait shack": "vt-sbs",
    "aj's grayton": "vt-aj",
    "aj's grayton beach": "vt-aj",
    "the pavilion at watersound town center": "vt-pav",
    "chiringo": "vt-chi",
    "30avenue": "vt-30a",
    "mcguire's destin": "vt-mcg",
    "mcguire’s destin": "vt-mcg",
    "crackings": "vt-crk",
    "local catch bar & grill": "vt-lcb",
    "seaside amphitheater": "vt-sea",
    "shunk gulley": "vt-sgl",
    "stinky's fish camp": "vt-sfc",
    "stinky’s fish camp": "vt-sfc",
    "the bay": "vt-bay",
    "the red bar": "vt-trb",
    "watersound town center": "vt-wsc",
    "rosemary beach": "vt-rsb",
    "rosemary beach town hall": "vt-rsb",
    "fish out of water": "vt-fow",
    "old florida fish house": "vt-ofh",
}

# Fallback "View event" link for venues whose observations never carry a
# url -- these post their schedules to Instagram (the same account their
# screenshot imports come from; see the aliases in canonical.py) rather than
# a working events page. Only used when an event has no observation url.
VENUE_FALLBACK_URL = {
    "papa surf": "https://instagram.com/papasurfburgerbar",
    "papa surf burger bar": "https://instagram.com/papasurfburgerbar",
    "shelby's beach bar": "https://instagram.com/shelbysbeachbar",
    "shelby's beach bar and gill": "https://instagram.com/shelbysbeachbar",
    "shelby's": "https://instagram.com/shelbysbeachbar",
}

# Venues whose plain name is ambiguous or shares a name with a business
# elsewhere; anything not listed falls back to searching the venue name as-is.
VENUE_MAPS_QUERY = {
    "aj's grayton": "AJ's Grayton Beach, FL",
    "aj's grayton beach": "AJ's Grayton Beach, FL",
    "the pavilion at watersound town center": "The Pavilion at Watersound Town Center, FL",
    "mcguire's destin": "McGuire's Irish Pub, Destin, FL",
    "mcguire’s destin": "McGuire's Irish Pub, Destin, FL",
}

# Shortened text for the venue badge only — display purposes, never applied to
# data-venue/aria-label/maps lookups, so filtering and directions still key
# off the real venue name. Anything not listed shows in full.
VENUE_DISPLAY_NAME = {
    "the pavilion at watersound town center": "The Pavilion at WTC",
}


def _venue_class(venue: str | None) -> str:
    return VENUE_CLASS.get((venue or "").strip().lower(), "vt-def")


def _venue_display_name(venue: str | None) -> str:
    v = (venue or "").strip()
    return VENUE_DISPLAY_NAME.get(v.lower(), v)


def _load_favorite_venues(csv_path: Path = VENUE_FAVORITES_CSV) -> set[str]:
    """
    Read venue_groups.csv into a set of lowercased favorite venue names.
    Missing file or an unlisted venue both default to not-favorite — that's
    a curation call only a human can make, not something inferable.
    """
    if not csv_path.exists():
        return set()
    with csv_path.open(newline="", encoding="utf-8") as f:
        return {
            (row.get("venue") or "").strip().lower()
            for row in csv.DictReader(f)
            if (row.get("venue") or "").strip()
        }


def _venue_favorite(venue: str | None, favorites: set[str]) -> bool:
    return (venue or "").strip().lower() in favorites


def _favorite_venue_names(csv_path: Path = VENUE_FAVORITES_CSV) -> list[str]:
    """
    Every venue listed in venue_groups.csv, original casing, sorted. Unlike
    the per-event data-favorite attribute (only present on rows that have an
    upcoming show), this is the full favorites roster regardless of whether a
    favorite currently has anything scheduled -- so the ★ Venues picker can
    list all of them, not just the ones with a booking right now.
    """
    if not csv_path.exists():
        return []
    with csv_path.open(newline="", encoding="utf-8") as f:
        names = [
            (row.get("venue") or "").strip()
            for row in csv.DictReader(f)
            if (row.get("venue") or "").strip()
        ]
    return sorted(names, key=str.lower)


def _load_performer_meta(csv_path: Path = ARTISTS_CSV) -> dict[str, bool]:
    """
    Read artists.csv into {performer_lower: favorite_bool}. Same
    "flat file as source of truth" pattern as _load_favorite_venues — missing
    file or an unlisted performer both default to not-favorite, since
    favoriting is a curation call only a human can make.
    """
    if not csv_path.exists():
        return {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        return {
            (row.get("performer") or "").strip().lower(): (row.get("favorite") or "").strip().upper() == "Y"
            for row in csv.DictReader(f)
            if (row.get("performer") or "").strip()
        }


def _performer_favorite(performer: str | None, meta: dict[str, bool]) -> bool:
    return bool(meta.get((performer or "").strip().lower()))


def _load_artist_order(csv_path: Path = ARTISTS_CSV) -> dict[str, int]:
    """
    {performer_lower: row_index} in the order performers appear in
    artists.csv, top to bottom. Used only to order same-date combo matches
    on the hero cards the way the roster is curated, rather than by an
    incidental tiebreaker like confidence. A performer not in the file (or
    a missing file) has no entry -- callers fall back to len(map) so
    unlisted performers sort after every listed one, not before.
    """
    if not csv_path.exists():
        return {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        return {
            (row.get("performer") or "").strip().lower(): i
            for i, row in enumerate(csv.DictReader(f))
            if (row.get("performer") or "").strip()
        }


def _favorite_performer_names(csv_path: Path = ARTISTS_CSV) -> list[str]:
    """Every performer marked favorite=Y in artists.csv, original casing, sorted.
    Same "full roster regardless of current bookings" reasoning as
    _favorite_venue_names() -- see its docstring."""
    if not csv_path.exists():
        return []
    with csv_path.open(newline="", encoding="utf-8") as f:
        names = [
            (row.get("performer") or "").strip()
            for row in csv.DictReader(f)
            if (row.get("favorite") or "").strip().upper() == "Y" and (row.get("performer") or "").strip()
        ]
    return sorted(names, key=str.lower)


def _all_venue_names(events: list[dict]) -> list[str]:
    """Every distinct venue name across all current events, original casing, sorted."""
    names = {(e.get("venue") or "").strip() for e in events if (e.get("venue") or "").strip()}
    return sorted(names, key=str.lower)


def _all_performer_names(events: list[dict]) -> list[str]:
    """Every distinct performer name across all current events, original casing, sorted."""
    names = {(e.get("performer") or "").strip() for e in events if (e.get("performer") or "").strip()}
    return sorted(names, key=str.lower)


def _venue_maps_urls(venue: str | None) -> tuple[str | None, str | None]:
    """(embed_url, external_url) for the venue's Google Maps modal. No API key needed."""
    v = (venue or "").strip()
    if not v:
        return None, None
    query = VENUE_MAPS_QUERY.get(v.lower(), v)
    encoded = quote_plus(query)
    return (
        f"https://www.google.com/maps?q={encoded}&output=embed",
        f"https://www.google.com/maps/search/?api=1&query={encoded}",
    )


def _band_class(score) -> str:
    return {"high": "cf-hi", "medium": "cf-md", "low": "cf-lo"}.get(
        confidence_band(score), "cf-md"
    )


def _fmt_date(iso: str | None) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%a %b %d")
    except (TypeError, ValueError):
        return iso or ""


def _obs_html(o: dict) -> str:
    src = html.escape(o.get("source") or "")
    otype = html.escape(o.get("observation_type") or "")
    conf = o.get("confidence")
    conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else "—"
    seen = (o.get("observed_at") or "")[:10]
    asserted = html.escape(o.get("time_start") or "")
    label = f"{src} <small>({otype})</small>"
    if o.get("url"):
        label = f'<a href="{html.escape(o["url"])}" target="_blank" rel="noopener">{src}</a> <small>({otype})</small>'
    return (
        f'<div class="ob"><span>✓ {label}</span>'
        f"<span>{asserted}</span><span>{conf_s}</span><span>{seen}</span></div>"
    )


def _rows_html(events: list[dict], path: Path) -> str:
    venue_favorites = _load_favorite_venues()
    performer_meta = _load_performer_meta()
    out = []
    for ev in events:
        performer_raw = ev.get("performer") or ev.get("name") or ""
        performer = html.escape(performer_raw)
        venue = ev.get("venue") or ""
        venue_e = html.escape(venue)
        time_s = html.escape(ev.get("time_start") or "")
        date = ev.get("date") or ""
        favorite = _venue_favorite(venue, venue_favorites)
        fav_attr = "Y" if favorite else "N"
        performer_fav_attr = "Y" if _performer_favorite(performer_raw, performer_meta) else "N"

        embed, ext = _venue_maps_urls(venue)
        embed_a = (embed or "").replace("&", "&amp;")
        ext_a = (ext or "").replace("&", "&amp;")

        star = '<span class="fav-star" aria-label="Favorite">★ </span>' if favorite else ""
        venue_display_e = html.escape(_venue_display_name(venue))
        badge = f'<span class="vt {_venue_class(venue)}">{star}{venue_display_e}</span>'
        venue_cell = (
            f'<a href="#" class="maplink" data-embed="{embed_a}" data-ext="{ext_a}" '
            f'data-vname="{venue_e}" aria-label="Get directions to {venue_e}">{badge}</a>'
            if embed else badge
        )

        # Highest-confidence observation with a URL (load_event_observations
        # already sorts by confidence DESC) becomes the tile's "View event"
        # link back to its original listing (SoWal, AJ's, etc).
        obs = load_event_observations(ev["id"], path) if ev.get("id") else []
        event_url = next((o["url"] for o in obs if o.get("url")), None) or VENUE_FALLBACK_URL.get(
            venue.lower()
        )
        event_url_a = html.escape(event_url) if event_url else ""

        # The report stays clean: no Sources/Confidence/Link columns. The source
        # listing and provenance are one click away — clicking a row reveals its
        # observations, each linking back to its original listing.
        out.append(
            f'<tr data-date="{date}" data-venue="{venue_e}" data-venue-display="{venue_display_e}" '
            f'data-performer="{performer}" '
            f'data-favorite="{fav_attr}" '
            f'data-performer-favorite="{performer_fav_attr}" data-embed="{embed_a}" data-ext="{ext_a}" '
            f'data-url="{event_url_a}">'
            f"<td><b>{performer}</b></td>"
            f"<td>{_fmt_date(date)}</td>"
            f"<td>{time_s}</td>"
            f"<td>{venue_cell}</td></tr>"
        )

        conf = ev.get("confidence")
        conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else "—"
        sc = ev.get("source_count") or 1
        detail = (
            f'<div class="ob"><span><b>Sources</b></span>'
            f'<span><span class="cf {_band_class(conf)}"><span class="d"></span>'
            f"confidence {conf_s}</span></span></div>"
        )
        detail += "".join(_obs_html(o) for o in obs)
        if ev.get("conflict_flag") and ev.get("conflict_reason"):
            detail += f'<div class="cflr">⚠ {html.escape(ev["conflict_reason"])}</div>'
        out.append(f'<tr class="exp"><td colspan="4">{detail}</td></tr>')
    return "\n".join(out)


def _health(events: list[dict], path: Path) -> dict:
    confs = [e["confidence"] for e in events if isinstance(e.get("confidence"), (int, float))]
    avg = round(sum(confs) / len(confs), 2) if confs else 0
    verified = sum(1 for e in events if (e.get("verification_count") or 0) > 1)
    conflicts = sum(1 for e in events if e.get("conflict_flag"))
    ids = [e["id"] for e in events if e.get("id")]
    sources = 0
    if ids:
        conn = get_connection(path)
        q = ("SELECT COUNT(DISTINCT source) FROM event_observations WHERE event_id IN (%s)"
             % ",".join("?" * len(ids)))
        sources = conn.execute(q, ids).fetchone()[0]
        conn.close()
    return {
        "total": len(events),
        "avgconf": f"{avg:.2f}",
        "verified": verified,
        "conflicts": conflicts,
        "sources": sources,
    }


def _pick_featured_group(
    events: list[dict], start_date: str, end_date: str, *, allow_ties_at_tier_0: bool = False,
) -> list[tuple[dict, bool, bool]]:
    """
    Best favorite-only match(es) within [start_date, end_date] (inclusive)
    -- a favorite-artist + favorite-venue combo outranks favorite-artist-
    only, which outranks favorite-venue-only. An event that's neither
    (tier 3) is never a candidate at all: these hero cards only ever
    headline an actual favorite, they don't fall back to "whatever's on".

    Normally returns just the single best match (date breaks ties within a
    tier, soonest first; same-date ties then follow the performer's row
    order in artists.csv, then confidence, then id). When
    allow_ties_at_tier_0 and the best tier present IS 0 (a combo), every
    tied combo match is returned instead of just one -- multiple genuinely
    favorite-artist-at-favorite-venue shows the same night are all worth
    surfacing, not just whichever happens to sort first. Returns [] when
    nothing in range qualifies.
    """
    venue_favorites = _load_favorite_venues()
    performer_meta = _load_performer_meta()
    artist_order = _load_artist_order()

    def tier_and_favs(e):
        venue_fav = _venue_favorite(e.get("venue"), venue_favorites)
        performer_fav = _performer_favorite(e.get("performer") or e.get("name"), performer_meta)
        if performer_fav and venue_fav:
            tier = 0
        elif performer_fav:
            tier = 1
        elif venue_fav:
            tier = 2
        else:
            tier = 3
        return tier, performer_fav, venue_fav

    scored = []
    for e in events:
        date = e.get("date") or ""
        if not (start_date <= date <= end_date):
            continue
        tier, performer_fav, venue_fav = tier_and_favs(e)
        if tier == 3:
            continue
        conf = e.get("confidence")
        conf = conf if isinstance(conf, (int, float)) else 0.0
        performer = (e.get("performer") or e.get("name") or "").strip().lower()
        order = artist_order.get(performer, len(artist_order))
        scored.append(((tier, date, order, -conf, e.get("id") or 0), e, performer_fav, venue_fav))

    if not scored:
        return []
    scored.sort(key=lambda t: t[0])
    best_tier = scored[0][0][0]
    if allow_ties_at_tier_0 and best_tier == 0:
        return [(e, pf, vf) for key, e, pf, vf in scored if key[0] == 0]
    _, ev, performer_fav, venue_fav = scored[0]
    return [(ev, performer_fav, venue_fav)]


def _hero_meta_html(ev: dict) -> str:
    """'at <b>Venue</b> · Today · 7:00 pm' -- pre-escaped HTML, safe to
    insert directly into the template."""
    today = datetime.now().strftime("%Y-%m-%d")
    when = "Today" if ev.get("date") == today else _fmt_date(ev.get("date"))
    venue = html.escape(_venue_display_name(ev.get("venue")))
    parts = [p for p in [when, ev.get("time_start")] if p]
    return f'at <b>{venue}</b> &middot; {html.escape(" • ".join(parts))}'


def _hero_more_html(extra: list[tuple[dict, bool, bool]], *, label: str = "Also tonight") -> str:
    """List of additional tied combo matches under the primary headline --
    e.g. two favorite-artist-at-favorite-venue shows the same night (or same
    week, for the week card) both get surfaced, not just whichever sorted
    first. Reuses _hero_meta_html() so an entry that falls on today reads
    "Today" instead of a redundant date, same as the primary headline
    does -- only entries on a later date within the week actually need one."""
    if not extra:
        return ""
    rows = []
    for ev, _performer_fav, _venue_fav in extra:
        performer = html.escape(ev.get("performer") or ev.get("name") or "Live Music")
        rows.append(
            '<div class="hero-more-item">'
            f'<div class="hero-more-name">{performer}</div>'
            f'<div class="hero-more-meta">{_hero_meta_html(ev)}</div>'
            "</div>"
        )
    return f'<div class="hero-more"><p class="hero-more-label">{html.escape(label)}</p>' + "".join(rows) + "</div>"


def _hero_card(
    group: list[tuple[dict, bool, bool]],
    *, kicker: str, empty_performer: str, empty_meta: str,
    show_extra: bool = False, more_label: str = "Also tonight",
) -> dict:
    """(kicker, performer, meta, extra) strings for one hero card. `group`
    is whatever _pick_featured_group() returned -- empty means no favorite
    qualified in that card's window, which gets its own honest empty state
    rather than silently falling back to a non-favorite show. The first
    entry headlines the card; any further tied entries render as a list
    (headed `more_label`) when show_extra is set."""
    if not group:
        return {"kicker": kicker, "performer": empty_performer, "meta": empty_meta, "extra": ""}
    ev, _performer_fav, _venue_fav = group[0]
    return {
        "kicker": kicker,
        "performer": html.escape(ev.get("performer") or ev.get("name") or "Live Music"),
        "meta": _hero_meta_html(ev),
        "extra": _hero_more_html(group[1:], label=more_label) if show_extra else "",
    }


def _build_marker() -> str:
    """
    Generation timestamp, baked server-side into the HTML at generate()
    time. Unlike the client-side "Updated <today>" badge (which always
    shows the VIEWER's current date regardless of page staleness), this is
    fixed at build time — the one thing in the page a screenshot can use to
    prove whether the browser is showing current or cached content.

    Deliberately NOT a git SHA: generate() always runs before the resulting
    file is committed, so `git rev-parse HEAD` at generation time is the
    PARENT of the commit this file ends up in, never its own hash (a commit
    cannot embed its own hash in its own tree). A SHA here reliably looks
    one commit stale and is misleading rather than useful for staleness
    checks -- burned real debugging time chasing a phantom CDN cache issue
    before this was diagnosed. The timestamp has no such self-reference
    problem and is sufficient on its own.
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"Build {stamp}"


def _json_for_script(value) -> str:
    """JSON-encode for embedding inside an inline <script> tag -- escapes '</'
    so a name containing it can't prematurely close the script element."""
    return json.dumps(value).replace("</", "<\\/")


def generate(out_path: Path = DEFAULT_OUT, run_id: str | None = None,
             path: Path = DB_PATH) -> Path:
    """Render the dashboard for current knowledge (or a specific run) into out_path."""
    template = TEMPLATE.read_text(encoding="utf-8")
    events = load_events(run_id=run_id, path=path) if run_id else load_current_events(path=path)
    # date ascending, then insertion order — matches the curated layout
    events.sort(key=lambda e: ((e.get("date") or ""), e.get("id") or 0))

    h = _health(events, path)

    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    week_end = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    tonight_group = _pick_featured_group(events, today, today, allow_ties_at_tier_0=True)
    week_group = _pick_featured_group(events, tomorrow, week_end, allow_ties_at_tier_0=True)

    hero_tonight = _hero_card(
        tonight_group,
        kicker="Tonight’s Featured Artist",
        empty_performer="No favorites tonight",
        empty_meta="Nothing starred for tonight — check the full lineup below.",
        show_extra=True,
        more_label="Also tonight",
    )
    hero_week = _hero_card(
        week_group,
        kicker="This Week’s Featured Artists",
        empty_performer="No favorites this week",
        empty_meta="Nothing starred coming up — check the full lineup below.",
        show_extra=True,
        more_label="Also this week",
    )

    out = (
        template
        .replace("TBODY_PLACEHOLDER", _rows_html(events, path))
        .replace("HERO_TONIGHT_KICKER_PLACEHOLDER", hero_tonight["kicker"])
        .replace("HERO_TONIGHT_PERFORMER_PLACEHOLDER", hero_tonight["performer"])
        .replace("HERO_TONIGHT_META_PLACEHOLDER", hero_tonight["meta"])
        .replace("HERO_TONIGHT_EXTRA_PLACEHOLDER", hero_tonight["extra"])
        .replace("HERO_WEEK_KICKER_PLACEHOLDER", hero_week["kicker"])
        .replace("HERO_WEEK_PERFORMER_PLACEHOLDER", hero_week["performer"])
        .replace("HERO_WEEK_META_PLACEHOLDER", hero_week["meta"])
        .replace("HERO_WEEK_EXTRA_PLACEHOLDER", hero_week["extra"])
        .replace("TOTAL_PLACEHOLDER", str(h["total"]))
        .replace("AVGCONF_PLACEHOLDER", h["avgconf"])
        .replace("VERIFIED_PLACEHOLDER", str(h["verified"]))
        .replace("CONFLICTS_PLACEHOLDER", str(h["conflicts"]))
        .replace("SOURCES_PLACEHOLDER", str(h["sources"]))
        .replace("BUILD_PLACEHOLDER", _build_marker())
        .replace("FAV_VENUES_PLACEHOLDER", _json_for_script(_favorite_venue_names()))
        .replace("FAV_ARTISTS_PLACEHOLDER", _json_for_script(_favorite_performer_names()))
        .replace("ALL_VENUES_PLACEHOLDER", _json_for_script(_all_venue_names(events)))
        .replace("ALL_ARTISTS_PLACEHOLDER", _json_for_script(_all_performer_names(events)))
    )
    out_path.write_text(out, encoding="utf-8")
    logger.info("Dashboard rendered to %s (%d events)", out_path, len(events))
    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    generate()
