#!/usr/bin/env python3
import os, sys, base64, sqlite3, pathlib
from datetime import datetime, date

try:
    import anthropic
except ImportError:
    os.system("pip3 install anthropic")
    import anthropic

def encode_image(img_path):
    with open(img_path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")

def extract_events(client, img_path):
    print(f"  Reading {img_path.name}...")
    ext = img_path.suffix.lower()
    media_type = "image/png" if ext == ".png" else "image/jpeg"
    data = encode_image(img_path)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role":"user","content":[
            {"type":"image","source":{"type":"base64","media_type":media_type,"data":data}},
            {"type":"text","text":"List every music event in this image. For each event give me one line: ARTIST|VENUE|DATE|TIME\nUse YYYY-MM-DD format for dates, assume year is 2026.\nIf venue not shown use the filename as venue.\nOutput ONLY the lines, no other text."}
        ]}]
    )
    events = []
    text = response.content[0].text.strip()
    print(f"    Raw: {text[:200]}")
    for line in text.splitlines():
        line = line.strip()
        if "|" not in line: continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3:
            artist = parts[0]
            venue = parts[1] if parts[1].upper() != "UNKNOWN" else img_path.stem.replace("_"," ").title()
            dt = parts[2]
            time = parts[3] if len(parts) > 3 else ""
            if artist and dt and dt.upper() != "UNKNOWN":
                events.append({"artist":artist,"venue":venue,"date":dt,"time":time})
                print(f"    + {artist} | {venue} | {dt} | {time}")
    print(f"    {len(events)} events found")
    return events

def normalize_names(db):
    fixes = [
        ("The Typos","THE TYPOS"),("Stevie Monce","STEVIE MONCE"),
        ("Casey Kearney","CASEY KEARNEY"),("Casey Kearney","Casey Kearney Band"),
        ("Brett Stafford","BRETT STAFFORD"),("Brett Stafford","Brett Stafford Smith"),
        ("Cadillac Willy","CADILLAC WILLY"),("Dion Jones","DION JONES & THE NEON TEARS"),
        ("Gage Cowart","GAGE COWART"),("Sunshine Wranglers","SUNSHINE WRANLGERS"),
        ("Sunshine Wranglers","The Sunshine Wranglers"),("Boukou Groove","BOUKOU GROOVE"),
        ("Harrison Prentice","HARRISON PRENTICE"),("Red Fish Taco","RED FISH TACO"),
        ("Papa Surf","Papa Surf Burger Bar"),("Papa Surf","PAPA SURF BURGER BAR"),
        ("North Beach Social","NORTH BEACH SOCIAL"),
        ("North Beach Social","North Beach Social 1"),("North Beach Social","North Beach Social 2"),
        ("30Avenue","30AVENUE"),
        ("Casey Kearney","CASEY KEARNEY BAND"),
        ("Casey Kearney","Casey Kearney Band"),
        ("Queens Handsome","QUEENS HANDSOME"),
        ("Papa Surf","Papa Surf Burger Bar 30A"),
        ("Papa Surf","Papa Surf Burger Bar"),
        ("Papa Surf","PAPA SURF BURGER BAR"),
        ("North Beach Social","July Live Music Lineup"),
        ("North Beach Social","july-live-music-lineup"),
        ("North Beach Social","JULY LIVE MUSIC LINEUP"),
        ("Lips Manly","LIPS MANLY"),
        ("Zack Miller","ZACK MILLER"),
        ("Pickled Pickers","PICKLED PICKERS"),
        ("Nate Kelly","NATE KELLY"),
        ("Zoe Walega","ZOE WALEGA"),
        ("River Dan","RIVER DAN"),
        ("Catalyst Dance Band","CATALYST DANCE BAND"),
        ("Black Creek String Band","BLACK CREEK STRING BAND"),
        ("Dion Jones","Dion Jones & The Neon Tears"),
        ("Bill Garrett","Bill Garrett + John Reinlie @ Brunch / Vine Illers"),
        ("Bill Garrett","Bill Garrett + John Reinlie @ Brunch / Corey Hall Trio"),
        ("Bill Garrett","Bill Garrett + John Reinlie @ Brunch / Harrison Prentice"),
        ("The Kennedys","The Kennedy\'s"),
    ]
    for canonical, old in fixes:
        db.execute("UPDATE events SET performer=? WHERE performer=?", (canonical,old))
        db.execute("UPDATE events SET venue=? WHERE venue=?", (canonical,old))
    # Default times for venues that don't always post times
    db.execute("UPDATE events SET time_start='6:00 - 9:00 PM' WHERE venue=\"Shelby's Beach Bar\"")
    db.execute("UPDATE events SET time_start='6:00 - 9:00 PM' WHERE venue='Papa Surf' AND (time_start IS NULL OR time_start='' OR UPPER(time_start) IN ('UNKNOWN','TBD','N/A'))")
    # Convert 24-hour times to 12-hour format
    import re
    rows = db.execute("SELECT id, time_start FROM events WHERE time_start IS NOT NULL AND time_start != ''").fetchall()
    for row_id, t in rows:
        if not t or 'AM' in t.upper() or 'PM' in t.upper(): continue
        rm = re.match(r'(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})', t)
        if rm:
            def fmt(h,m): return f"{h%12 or 12}:{m} {'AM' if int(h)<12 else 'PM'}"
            db.execute("UPDATE events SET time_start=? WHERE id=?", (fmt(rm.group(1),rm.group(2))+' - '+fmt(rm.group(3),rm.group(4)), row_id))
        else:
            sm = re.match(r'(\d{1,2}):(\d{2})$', t)
            if sm:
                h,m = int(sm.group(1)),sm.group(2)
                db.execute("UPDATE events SET time_start=? WHERE id=?", (f"{h%12 or 12}:{m} {'AM' if h<12 else 'PM'}", row_id))
    db.execute("UPDATE events SET performer=TRIM(performer)")
    db.execute("UPDATE events SET venue=TRIM(venue)")
    db.execute("DELETE FROM events WHERE id NOT IN (SELECT MIN(id) FROM events GROUP BY performer, date, venue)")
    db.commit()

def setup_db():
    pathlib.Path("data").mkdir(exist_ok=True)
    db = sqlite3.connect("data/events.db")
    db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, date TEXT, time_start TEXT, time_end TEXT, venue TEXT, performer TEXT, url TEXT)")
    db.commit()
    return db

def rebuild_dashboard(db):
    rows = db.execute("SELECT name,date,time_start,venue,performer,url FROM events ORDER BY date,time_start").fetchall()
    lines = []
    for r in rows:
        name,dt,t1,venue,performer,url = r
        try: dfmt = datetime.strptime(dt,"%Y-%m-%d").strftime("%a %b %d")
        except: dfmt = dt or ""
        a = (performer or name or "").replace('"',"'")
        v = (venue or "").replace('"',"'")
        line = ('<tr data-name="' + a + '" data-date="' + (dt or "") + 
                '" data-venue="' + v + '" data-performer="' + a + '">' +
                '<td><strong>' + (performer or name or "") + '</strong></td>' +
                '<td>' + dfmt + '</td><td>' + (t1 or "") + '</td>' +
                '<td><span style="background:#f0fdf4;color:#166534;padding:3px 10px;border-radius:8px;font-size:1rem;font-weight:500">' + (venue or "") + '</span></td>' +
                '<td><a href="' + (url or "#") + '" target="_blank" style="color:#1B7A8A;font-size:.85rem">view</a></td></tr>')
        lines.append(line)
    tbody = "\n".join(lines)
    pathlib.Path("data/index.html").write_text(build_html(tbody, len(rows)))
    print(f"Dashboard rebuilt with {len(rows)} events")

def build_html(tbody, total):
    return open("data/template.html").read().replace("TBODY_PLACEHOLDER", tbody).replace("TOTAL_PLACEHOLDER", str(total))

if __name__ == "__main__":
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set"); sys.exit(1)
    client = anthropic.Anthropic(api_key=api_key)
    inbox = pathlib.Path("images/inbox")
    processed = pathlib.Path("images/processed")
    inbox.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)
    images = sorted([f for f in inbox.glob("*") if f.suffix.upper() in (".PNG",".JPG",".JPEG")])
    if not images:
        print("No images in images/inbox/"); sys.exit(0)
    print(f"Found {len(images)} images\n")
    all_events = []
    for img in images:
        all_events.extend(extract_events(client, img))
    print(f"\nTotal: {len(all_events)} events")
    db = setup_db()
    # Keep existing events, just add new ones (duplicates handled by date+performer+venue)
    for ev in all_events:
        a,v,dt,t = ev["artist"],ev["venue"],ev["date"],ev["time"]
        url = "https://instagram.com/" + a.lower().replace(" ","").replace("'","")
        db.execute("INSERT INTO events (name,date,time_start,venue,performer,url) VALUES (?,?,?,?,?,?)",
            (a+" at "+v, dt, t, v, a, url))
    db.commit()
    normalize_names(db)
    rebuild_dashboard(db)
    print(f"Total in DB: {db.execute('SELECT COUNT(*) FROM events').fetchone()[0]}")
    db.close()
    for img in images:
        img.rename(processed/img.name)
    print(f"Moved {len(images)} images to processed/")
    print("Done!")
