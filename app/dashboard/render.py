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
import logging
import subprocess
from datetime import datetime
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

# Editable venue -> region grouping, maintained by hand as a flat CSV (same
# "flat file as source of truth" pattern as venues.txt/artists.txt) rather
# than a code-edited table, so groupings can be corrected without touching
# Python. Blank/unlisted venues fall back to "Other" — see _venue_group().
VENUE_GROUPS_CSV = Path("app/dashboard/venue_groups.csv")

# Editable performer favorites, same flat-CSV pattern as VENUE_GROUPS_CSV but
# without a group column — there's no regional grouping for performers, just
# a curated favorite/not-favorite call. Unlisted performers default to N.
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


def _load_venue_meta(csv_path: Path = VENUE_GROUPS_CSV) -> dict[str, dict]:
    """
    Read venue_groups.csv into {venue_lower: {"group": str, "favorite": bool}}.
    Missing file, an unlisted venue, or a blank cell all take the same default
    (see _venue_group / _venue_favorite) — favorite defaults to False since
    it's a curation call only a human can make, not something inferable.
    """
    if not csv_path.exists():
        return {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        return {
            (row.get("venue") or "").strip().lower(): {
                "group": (row.get("group") or "").strip(),
                "favorite": (row.get("favorite") or "").strip().upper() == "Y",
            }
            for row in csv.DictReader(f)
            if (row.get("venue") or "").strip()
        }


def _venue_group(venue: str | None, meta: dict[str, dict]) -> str:
    return (meta.get((venue or "").strip().lower()) or {}).get("group") or "Other"


def _venue_favorite(venue: str | None, meta: dict[str, dict]) -> bool:
    return bool((meta.get((venue or "").strip().lower()) or {}).get("favorite"))


def _load_performer_meta(csv_path: Path = ARTISTS_CSV) -> dict[str, bool]:
    """
    Read artists.csv into {performer_lower: favorite_bool}. Same
    "flat file as source of truth" pattern as _load_venue_meta — missing file
    or an unlisted performer both default to not-favorite, since favoriting
    is a curation call only a human can make.
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
    venue_meta = _load_venue_meta()
    performer_meta = _load_performer_meta()
    out = []
    for ev in events:
        performer_raw = ev.get("performer") or ev.get("name") or ""
        performer = html.escape(performer_raw)
        venue = ev.get("venue") or ""
        venue_e = html.escape(venue)
        time_s = html.escape(ev.get("time_start") or "")
        date = ev.get("date") or ""
        region = html.escape(_venue_group(venue, venue_meta))
        favorite = _venue_favorite(venue, venue_meta)
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

        # The report stays clean: no Sources/Confidence/Link columns. The source
        # listing and provenance are one click away — clicking a row reveals its
        # observations, each linking back to its original listing.
        out.append(
            f'<tr data-date="{date}" data-venue="{venue_e}" data-venue-display="{venue_display_e}" '
            f'data-performer="{performer}" '
            f'data-region="{region}" data-favorite="{fav_attr}" '
            f'data-performer-favorite="{performer_fav_attr}" data-embed="{embed_a}" data-ext="{ext_a}">'
            f"<td><b>{performer}</b></td>"
            f"<td>{_fmt_date(date)}</td>"
            f"<td>{time_s}</td>"
            f"<td>{venue_cell}</td></tr>"
        )

        conf = ev.get("confidence")
        conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else "—"
        sc = ev.get("source_count") or 1
        obs = load_event_observations(ev["id"], path) if ev.get("id") else []
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


def _build_marker() -> str:
    """
    Short git SHA + generation timestamp, baked server-side into the HTML at
    generate() time. Unlike the client-side "Updated <today>" badge (which
    always shows the VIEWER's current date regardless of page staleness),
    this is fixed at build time — the one thing in the page a screenshot can
    use to prove whether the browser is showing current or cached content.
    """
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except Exception:
        sha = "unknown"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"Build {sha} · {stamp}"


def generate(out_path: Path = DEFAULT_OUT, run_id: str | None = None,
             path: Path = DB_PATH) -> Path:
    """Render the dashboard for current knowledge (or a specific run) into out_path."""
    template = TEMPLATE.read_text(encoding="utf-8")
    events = load_events(run_id=run_id, path=path) if run_id else load_current_events(path=path)
    # date ascending, then insertion order — matches the curated layout
    events.sort(key=lambda e: ((e.get("date") or ""), e.get("id") or 0))

    h = _health(events, path)
    out = (
        template
        .replace("TBODY_PLACEHOLDER", _rows_html(events, path))
        .replace("TOTAL_PLACEHOLDER", str(h["total"]))
        .replace("AVGCONF_PLACEHOLDER", h["avgconf"])
        .replace("VERIFIED_PLACEHOLDER", str(h["verified"]))
        .replace("CONFLICTS_PLACEHOLDER", str(h["conflicts"]))
        .replace("SOURCES_PLACEHOLDER", str(h["sources"]))
        .replace("BUILD_PLACEHOLDER", _build_marker())
    )
    out_path.write_text(out, encoding="utf-8")
    logger.info("Dashboard rendered to %s (%d events)", out_path, len(events))
    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    generate()
