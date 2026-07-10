"""
app/dashboard/build_template.py
One-time tool: derive app/dashboard/template.html from the pristine, hand-built
docs/index.html.

It preserves the shell EXACTLY (CSS, SVG map, legend, filters, existing JS) and
makes only additive changes:
  - {{TOTAL}} / {{AVGCONF}} / {{CONFLICTS}} / {{SOURCES}} health cells in the stats bar
  - two new table columns (Sources, Conf)
  - {{TBODY}} and {{TONIGHT}} placeholders
  - small CSS for confidence badges / source chips / expand rows
  - a tiny expand-toggle JS + a one-line guard so row filtering skips detail rows

Run once:  python -m app.dashboard.build_template
"""
from pathlib import Path

SRC = Path("docs/index.html")
OUT = Path("app/dashboard/template.html")

EXTRA_CSS = (
    ".cf{font-weight:700;font-size:.82rem;white-space:nowrap;}"
    ".cf .d{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:4px;vertical-align:middle;}"
    ".cf-hi .d{background:#16a34a;}.cf-md .d{background:#f59e0b;}.cf-lo .d{background:#dc2626;}"
    ".src{font-size:.8rem;color:#16a085;letter-spacing:1px;white-space:nowrap;}"
    ".cfl{color:#dc2626;font-weight:700;margin-left:4px;}"
    ".xp{cursor:pointer;color:#1B7A8A;font-weight:700;user-select:none;margin-left:6px;}"
    "tr.exp td{background:#f8fbff;font-size:.8rem;color:#334155;padding:8px 14px;}"
    "tr.exp .ob{display:flex;justify-content:space-between;gap:10px;padding:3px 0;border-bottom:1px dotted #e2e8f0;}"
    "tr.exp .cflr{color:#b91c1c;font-weight:600;margin-top:6px;}"
)

TOGGLE_JS = (
    "function tog(e){var tr=e.target.closest('tr');var d=tr.nextElementSibling;"
    "if(d&&d.classList.contains('exp')){d.style.display=(d.style.display==='table-row')?'none':'table-row';}}"
)


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"[{label}] expected exactly 1 occurrence, found {n}")
    return text.replace(old, new)


def build() -> None:
    html = SRC.read_text(encoding="utf-8")

    # 1. Total -> placeholder
    html = _replace_once(
        html,
        '<div class="n">262</div><div class="l">Total</div>',
        '<div class="n">{{TOTAL}}</div><div class="l">Total</div>',
        "total",
    )

    # 2. Append health cells after the Artists stat (end of stats bar)
    html = _replace_once(
        html,
        '<div class="stat"><div class="n" id="sa">0</div><div class="l">Artists</div></div></div>',
        '<div class="stat"><div class="n" id="sa">0</div><div class="l">Artists</div></div>'
        '<div class="stat"><div class="n">{{AVGCONF}}</div><div class="l">Avg Conf</div></div>'
        '<div class="stat"><div class="n">{{CONFLICTS}}</div><div class="l">Conflicts</div></div>'
        '<div class="stat"><div class="n">{{SOURCES}}</div><div class="l">Sources</div></div></div>',
        "health-cells",
    )

    # 3. Add Sources + Conf columns to the header
    html = _replace_once(
        html,
        '<thead><tr><th>Artist</th><th>Date</th><th class="hm">Time</th><th>Venue</th><th class="hm">Link</th></tr></thead>',
        '<thead><tr><th>Artist</th><th>Date</th><th class="hm">Time</th><th>Venue</th><th class="hm">Link</th>'
        '<th class="hm">Sources</th><th>Conf</th></tr></thead>',
        "thead",
    )

    # 4. tbody rows -> placeholder
    a = html.index('<tbody id="tb">') + len('<tbody id="tb">')
    b = html.index("</tbody>", a)
    html = html[:a] + "{{TBODY}}" + html[b:]

    # 5. tonight card -> placeholder (from `.tn` open up to the legend)
    tn = html.index('<div class="tn"')
    leg = html.index('<div class="leg">', tn)
    html = html[:tn] + "{{TONIGHT}}\n" + html[leg:]

    # 6. Extra CSS before </style>
    html = _replace_once(html, "</style>", EXTRA_CSS + "</style>", "css")

    # 7. Guard: filtering/tonight loops skip detail (.exp) rows
    guard_old = "document.querySelectorAll('#tb tr').forEach(function(r){"
    guard_new = guard_old + "if(r.classList.contains('exp'))return;"
    count = html.count(guard_old)
    if count < 1:
        raise RuntimeError("guard anchor not found")
    html = html.replace(guard_old, guard_new)

    # 8. Expand-toggle JS before </script>
    html = _replace_once(
        html, "</script></body></html>", TOGGLE_JS + "\n</script></body></html>", "toggle-js"
    )

    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT} ({len(html)} bytes); guard applied to {count} loops")


if __name__ == "__main__":
    build()
