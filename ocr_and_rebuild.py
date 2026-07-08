#!/usr/bin/env python3
import pathlib, re, sqlite3
from datetime import datetime, date

try:
    import Vision
    from Foundation import NSURL
except ImportError:
    print("Run: pip3 install pyobjc-framework-Vision")
    exit(1)

def ocr_with_positions(img_path):
    url = NSURL.fileURLWithPath_(str(img_path.absolute()))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(1)
    handler.performRequests_error_([request], None)
    items = []
    for obs in request.results():
        box = obs.boundingBox()
        text = obs.topCandidates_(1)[0].string()
        items.append({"x": box.origin.x, "y": box.origin.y, "text": text})
    return items

def parse_two_column(items, artist, year=None):
    if year is None: year = date.today().year
    left  = sorted([i for i in items if i["x"] < 0.45],  key=lambda i: -i["y"])
    right = sorted([i for i in items if i["x"] >= 0.45], key=lambda i: -i["y"])
    events = []
    for column in [left, right]:
        current_date = None
        current_venue = None
        current_time = None
        for item in column:
            text = item["text"].strip()
            upper = text.upper()
            if len(text) < 3: continue
            if any(c in text for c in ["\u00ff","\u00a5","\u00a2","%"]): continue
            dm = re.search(r"(?:MON|TUE|WED|THU|FRI|SAT|SUN)\w*\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\w*\s+(\d{1,2})", upper)
            if dm:
                try:
                    month = dm.group(1)[:3].title()
                    day = int(dm.group(2))
                    current_date = datetime.strptime(f"{month} {day} {year}", "%b %d %Y").date()
                    current_venue = None
                    current_time = None
                except: pass
                continue
            tm = re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:AM|PM))\b", upper)
            if tm:
                current_time = tm.group(1).strip()
                if current_date and current_venue and current_time:
                    if current_date >= date.today():
                        events.append({"name": f"{artist} at {current_venue}", "date": current_date.isoformat(), "time": current_time, "venue": current_venue, "performer": artist})
                        print(f"  MATCH: {artist} | {current_date} | {current_time} | {current_venue}")
                    current_venue = None
                    current_time = None
                continue
            venue = None
            if "CHIRINGO" in upper: venue = "Chiringo"
            elif "RED FISH" in upper or upper.startswith("ED FIS"): venue = "Red Fish Taco"
            elif "PAPA SURF" in upper: venue = "Papa Surf"
            elif "SHELBY" in upper: venue = "Shelby's Beach Bar"
            elif "BIG CHILL" in upper: venue = "The Big Chill"
            elif "WATERCOLOR" in upper: venue = "Watercolor Beach Club"
            elif "WATERSOUND" in upper: venue = "Watersound Beach"
            elif "HAUGHTY" in upper: venue = "Haughty Heron"
            elif "SCALLOP" in upper: venue = "Scallop Republic"
            elif "STINKY" in upper: venue = "Stinky's Bait Shack"
            elif "ALIBI" in upper: venue = "Alibi Beach Lounge"
            elif "MOES" in upper or "MOE'S" in upper: venue = "Moe's BBQ"
            elif "THE DOCK" in upper: venue = "The Dock"
            elif "AJ" in upper and "GRAYTON" in upper: venue = "AJ's Grayton"
            elif "SHUNK" in upper: venue = "Shunk Gulley"
            elif "OUTCAST" in upper: venue = "Outcast"
            elif "PROPS" in upper: venue = "Props Brewery SRB"
            if venue: current_venue = venue
    return events

def rebuild_dashboard():
    db = sqlite3.connect("data/events.db")
    rows = db.execute("SELECT name,date,time_start,time_end,venue,performer,url FROM events ORDER BY date,time_start").fetchall()
    db.close()
    today = date.today().isoformat()
    tbody = ""
    for r in rows:
        name, dt, t1, t2, venue, performer, url = r
        try: dfmt = datetime.strptime(dt, "%Y-%m-%d").strftime("%a %b %d")
        except: dfmt = dt or ""
        cls = "up" if (dt or "") >= today else ""
        n = (name or "").replace('"',"\'")
        v = (venue or "").replace('"',"\'")
        p = (performer or "").replace('"',"\'")
        tbody += f'<tr class="{cls}" data-name="{n}" data-date="{dt or ""}" data-venue="{v}" data-performer="{p}"><td><strong>{name or ""}</strong></td><td>{dfmt}</td><td>{t1 or ""}</td><td><span style="background:#f0fdf4;color:#166534;padding:2px 8px;border-radius:8px;font-size:.72rem">{venue or ""}</span></td><td><span style="background:#e0f2fe;color:#0369a1;padding:2px 8px;border-radius:8px;font-size:.72rem">{performer or ""}</span></td><td><a href="{url or "#"}" target="_blank" style="color:#1B7A8A;font-size:.8rem">view</a></td></tr>\n'
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>30A Music Intelligence</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:-apple-system,sans-serif;background:#F5F0E8;}}
header{{background:#1F3864;color:#fff;padding:16px 24px;}}
header h1{{font-size:1.3rem;margin:0;}}
header p{{font-size:.72rem;opacity:.6;margin-top:3px;}}
.wrap{{padding:20px;}}
.controls{{display:flex;gap:10px;margin-bottom:16px;align-items:center;flex-wrap:wrap;}}
input{{padding:8px 12px;border:1px solid #ddd;border-radius:5px;font-size:.9rem;width:280px;outline:none;}}
select,button{{padding:8px 12px;border:1px solid #ddd;border-radius:5px;background:#fff;cursor:pointer;font-size:.85rem;}}
button:hover{{background:#eee;}}
.cnt{{color:#666;font-size:.85rem;margin-left:auto;}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,.1);overflow:hidden;}}
th{{background:#1F3864;color:#fff;padding:11px 14px;text-align:left;font-size:.8rem;cursor:pointer;white-space:nowrap;}}
th:hover{{background:#1B7A8A;}}
th.asc::after{{content:" ▲";font-size:.6rem;opacity:.7;}}
th.desc::after{{content:" ▼";font-size:.6rem;opacity:.7;}}
td{{padding:10px 14px;border-bottom:1px solid #f0f0f0;font-size:.85rem;vertical-align:middle;}}
tr:hover td{{background:#f0f7ff;}}
tr.up td{{background:rgba(27,122,138,.04);}}
tr.up td:first-child{{border-left:3px solid #1B7A8A;}}
a{{color:#1B7A8A;text-decoration:none;}}
a:hover{{text-decoration:underline;}}
.empty{{text-align:center;padding:40px;color:#aaa;}}
</style></head>
<body>
<header>
  <h1>🎵 30A Music Intelligence</h1>
  <p>{len(rows)} events · Updated {datetime.now().strftime("%b %d %Y %I:%M %p")}</p>
</header>
<div class="wrap">
  <div class="controls">
    <input type="search" id="q" placeholder="Search events, artists, venues..." oninput="go()">
    <select id="df" onchange="go()">
      <option value="all" selected>All dates</option>
      <option value="up">Upcoming only</option>
      <option value="past">Past only</option>
    </select>
    <button onclick="document.getElementById('q').value='';document.getElementById('df').value='all';go()">Clear</button>
    <span class="cnt" id="cnt">{len(rows)} events</span>
  </div>
  <table>
    <thead><tr>
      <th onclick="srt('name')">Event</th>
      <th onclick="srt('date')">Date</th>
      <th>Time</th>
      <th onclick="srt('venue')">Venue</th>
      <th onclick="srt('performer')">Artist</th>
      <th>Link</th>
    </tr></thead>
    <tbody id="tb">{tbody if tbody else '<tr><td colspan="6" class="empty">No events yet.</td></tr>'}</tbody>
  </table>
</div>
<script>
var TODAY="{today}";var sk='date',sd=1;
function go(){{var q=document.getElementById('q').value.toLowerCase();var df=document.getElementById('df').value;var rows=document.querySelectorAll('#tb tr');var n=0;rows.forEach(function(tr){{var show=true;var dt=tr.getAttribute('data-date')||''  ;var txt=(tr.getAttribute('data-name')||'')+' '+(tr.getAttribute('data-venue')||'')+' '+(tr.getAttribute('data-performer')||''  );if(df==='up'&&dt<TODAY)show=false;if(df==='past'&&dt>=TODAY)show=false;if(q&&txt.toLowerCase().indexOf(q)<0)show=false;tr.style.display=show?'':'none';if(show)n++;}});document.getElementById('cnt').textContent=n+' events';}}
function srt(k){{var tb=document.getElementById('tb');var rows=Array.from(tb.querySelectorAll('tr'));if(sk===k){{sd*=-1;}}else{{sk=k;sd=1;}}var m={{'name':'data-name','date':'data-date','venue':'data-venue','performer':'data-performer'}};rows.sort(function(a,b){{var av=a.getAttribute(m[k])||''  ,bv=b.getAttribute(m[k])||''  ;return av<bv?-sd:av>bv?sd:0;}});rows.forEach(function(r){{tb.appendChild(r);}});document.querySelectorAll('thead th').forEach(function(t){{t.classList.remove('asc','desc');}});var th=document.querySelector('th[onclick="srt(\\'"+k+"\\')"]');if(th)th.classList.add(sd===1?'asc':'desc');}}
go();
</script></body></html>"""
    pathlib.Path("data/index.html").write_text(html)
    print(f"Dashboard rebuilt with {len(rows)} events")

pathlib.Path("data").mkdir(exist_ok=True)
db = sqlite3.connect("data/events.db")
db.execute("""CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, date TEXT, time_start TEXT, time_end TEXT, venue TEXT, performer TEXT, url TEXT)""")
db.commit()
db.close()

images = [f for f in pathlib.Path("schedules").glob("*") if f.suffix.lower() in ('.png','.jpg','.jpeg')]
print(f"Found {len(images)} screenshots")
all_events = []
for img in images:
    artist = img.stem.replace("_"," ").title()
    print(f"\n=== {artist} ===")
    items = ocr_with_positions(img)
    events = parse_two_column(items, artist)
    if not events: print("  (no matches)")
    all_events.extend(events)
print(f"\nExtracted {len(all_events)} events")
db = sqlite3.connect("data/events.db")
db.execute("DELETE FROM events WHERE performer IS NOT NULL AND performer != ''")
for ev in all_events:
    db.execute("INSERT INTO events (name,date,time_start,venue,performer,url) VALUES (?,?,?,?,?,?)",
        (ev["name"],ev["date"],ev["time"],ev["venue"],ev["performer"],f"https://instagram.com/{ev['performer'].lower().replace(' ','')}"))
db.commit()
print(f"Saved. Total: {db.execute('SELECT COUNT(*) FROM events').fetchone()[0]}")
db.close()
rebuild_dashboard()
print("\nDone! Start server: cd data && python3 -m http.server 8080")
