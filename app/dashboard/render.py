"""
app/dashboard/render.py
Dumb dashboard renderer.

Reads canonical events + their observations from the database and fills
app/dashboard/template.html. It ONLY renders precomputed values — it never
computes confidence, reconciliation, venue defaults, or canonical names.
"""
import html
import logging
from datetime import date, datetime
from pathlib import Path

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

# Venue -> colour-chip class, matching the hand-built dashboard exactly.
VENUE_CLASS = {
    "The Pavilion at Watersound Town Center": "vt-pav",
    "Red Fish Taco": "vt-rft",
    "AJ's Grayton": "vt-aj",
    "North Beach Social": "vt-nbs",
    "Stinky's Bait Shack": "vt-sbs",
    "Shelby's Beach Bar": "vt-sbb",
    "Papa Surf": "vt-ps",
    "30Avenue": "vt-30a",
    "McGuire's Destin": "vt-mcg",
    "Chiringo": "vt-chi",
}


def _venue_class(venue: str | None) -> str:
    return VENUE_CLASS.get(venue or "", "vt-def")


def _band_class(score) -> str:
    return {"high": "cf-hi", "medium": "cf-md", "low": "cf-lo"}.get(confidence_band(score), "cf-md")


def _fmt_date(iso: str | None) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%a %b %d")
    except (TypeError, ValueError):
        return iso or ""


def _obs_row_html(o: dict) -> str:
    src = html.escape(o.get("source") or "")
    otype = html.escape(o.get("observation_type") or "")
    conf = o.get("confidence")
    conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else "—"
    seen = (o.get("observed_at") or "")[:10]
    label = f"✓ {src} <small>({otype})</small>"
    if o.get("url"):
        label = f'✓ <a href="{html.escape(o["url"])}" target="_blank">{src}</a> <small>({otype})</small>'
    return f'<div class="ob"><span>{label}</span><span>{conf_s}</span><span>{seen}</span></div>'


def _event_rows_html(events: list[dict], path: Path) -> str:
    out = []
    for ev in events:
        performer = html.escape(ev.get("performer") or "")
        venue = ev.get("venue") or ""
        venue_e = html.escape(venue)
        vclass = _venue_class(venue)
        time_s = html.escape(ev.get("time_start") or "")
        url = ev.get("url") or "#"
        conf = ev.get("confidence")
        conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else "—"
        band = _band_class(conf)

        sc = ev.get("source_count") or 1
        vc = ev.get("verification_count") or sc
        checks = "✓" * min(sc, 5)
        conflict = ev.get("conflict_flag")
        cfl = f'<span class="cfl" title="{html.escape(ev.get("conflict_reason") or "")}">⚠</span>' if conflict else ""
        src_cell = f'<span class="src" title="{vc} verify / {sc} sources">{checks} ({sc})</span>{cfl}'

        out.append(
            f'<tr data-date="{ev.get("date") or ""}" data-venue="{venue_e}" data-performer="{performer}">'
            f"<td><b>{performer}</b></td>"
            f"<td>{_fmt_date(ev.get('date'))}</td>"
            f"<td>{time_s}</td>"
            f'<td><span class="vt {vclass}">{venue_e}</span></td>'
            f'<td><a href="{html.escape(url)}" target="_blank">view</a></td>'
            f"<td>{src_cell}</td>"
            f'<td><span class="cf {band}"><span class="d"></span>{conf_s}</span>'
            f'<span class="xp" onclick="tog(this)">▸</span></td></tr>'
        )

        obs = load_event_observations(ev["id"], path) if ev.get("id") else []
        detail = "".join(_obs_row_html(o) for o in obs)
        if ev.get("conflict_flag") and ev.get("conflict_reason"):
            detail += f'<div class="cflr">⚠ {html.escape(ev["conflict_reason"])}</div>'
        out.append(f'<tr class="exp"><td colspan="7">{detail}</td></tr>')
    return "\n".join(out)


def _tonight_html(events: list[dict], today: str) -> str:
    d = date.today()
    label = d.strftime("%A, %B %-d")
    todays = [e for e in events if (e.get("date") or "") == today]
    parts = [f'<div class="tn" data-built="{today}"><h2>Tonight — {label}</h2>']
    if not todays:
        parts.append('<p style="opacity:.6">No shows tonight — check This Week</p>')
    else:
        for e in todays:
            performer = html.escape(e.get("performer") or "")
            venue = e.get("venue") or ""
            parts.append(
                f'<div class="sc"><div><b>{performer}</b><br><small>{html.escape(e.get("time_start") or "")}</small></div>'
                f'<span class="vt {_venue_class(venue)}">{html.escape(venue)}</span></div>'
            )
    parts.append("</div>")
    return "".join(parts)


def _health(events: list[dict], path: Path) -> dict:
    confs = [e["confidence"] for e in events if isinstance(e.get("confidence"), (int, float))]
    avg = round(sum(confs) / len(confs), 2) if confs else 0
    conflicts = sum(1 for e in events if e.get("conflict_flag"))
    ids = [e["id"] for e in events if e.get("id")]
    n_sources = 0
    if ids:
        conn = get_connection(path)
        q = "SELECT COUNT(DISTINCT source) FROM event_observations WHERE event_id IN (%s)" % ",".join("?" * len(ids))
        n_sources = conn.execute(q, ids).fetchone()[0]
        conn.close()
    return {"total": len(events), "avgconf": f"{avg:.2f}", "conflicts": conflicts, "sources": n_sources}


def generate(out_path: Path = DEFAULT_OUT, run_id: str | None = None, path: Path = DB_PATH) -> Path:
    """Render the dashboard for a run (default: latest) into out_path."""
    template = TEMPLATE.read_text(encoding="utf-8")
    # Default: current knowledge across all runs (latest-wins per identity). A
    # specific run_id can be passed for reproduction/testing.
    events = load_events(run_id=run_id, path=path) if run_id else load_current_events(path=path)
    # date ascending, then insertion order (id) — reproduces the curated layout;
    # within-day ordering follows how events were added, as the original did.
    events.sort(key=lambda e: ((e.get("date") or ""), e.get("id") or 0))

    today = date.today().isoformat()
    health = _health(events, path)

    html_out = (
        template
        .replace("{{TBODY}}", _event_rows_html(events, path))
        .replace("{{TONIGHT}}", _tonight_html(events, today))
        .replace("{{TOTAL}}", str(health["total"]))
        .replace("{{AVGCONF}}", health["avgconf"])
        .replace("{{CONFLICTS}}", str(health["conflicts"]))
        .replace("{{SOURCES}}", str(health["sources"]))
    )
    out_path.write_text(html_out, encoding="utf-8")
    logger.info("Dashboard rendered to %s (%d events)", out_path, len(events))
    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    generate()
