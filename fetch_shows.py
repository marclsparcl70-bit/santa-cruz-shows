#!/usr/bin/env python3
"""Fetch upcoming live-music events from Santa Cruz-area venues into events.json.

Standard library only. Run:  python3 fetch_shows.py [output_path]
Each venue is scraped independently; one broken site never stops the others.
"""
import hashlib
import html
import os
import json
import re
import sys
import urllib.parse
import urllib.request
import http.cookiejar
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))
_opener.addheaders = [("User-Agent", UA), ("Accept-Language", "en-US")]


def get(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with _opener.open(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse_time(s):
    """'8:00 PM' / '8 pm' / '8pm' -> 'HH:MM' or None."""
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(?:-\s*\d{1,2}(?::\d{2})?\s*)?([ap])\.?m", s or "", re.I)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
    if ap == "p" and h != 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}"


def event(venue, title, day, time=None, url=None, price=None, info=None, image=None):
    return {
        "venue": venue,
        "title": title,
        "date": day.isoformat() if isinstance(day, date) else day,
        "time": time,
        "url": url,
        "price": price,
        "info": (info[:137].rsplit(" ", 1)[0] + "…") if info and len(info) > 140 else info,
        "image": image,
    }


# ---------- venues ----------

def catalyst():
    out, seen_first = [], set()
    for page in range(1, 8):
        url = "https://catalystclub.com/events/" + (f"page/{page}/" if page > 1 else "")
        h = get(url)
        blocks = h.split('class="col-12 col-sm-6 col-lg-4 eventMainWrapper')[1:]
        first = re.search(r'href="(https://catalystclub\.com/event/[^"]+)"', blocks[0]) if blocks else None
        if not blocks or not first or first.group(1) in seen_first:
            break  # site serves page 1 again once it runs out
        seen_first.add(first.group(1))
        today = date.today()
        for b in blocks:
            link = re.search(r'id\s*=\s*"eventTitle"[^>]*href="([^"]+)"', b) or re.search(r'href="(https://catalystclub\.com/event/[^"]+)"', b)
            title = re.search(r"<h2[^>]*>(.*?)</h2>", b, re.S)
            d = re.search(r'singleEventDate[^>]*>\s*([^<]+)<', b)
            if not (title and d):
                continue
            m = re.search(r"([A-Z][a-z]{2})\s+(\d{1,2})", d.group(1))
            if not m:
                continue
            mon = datetime.strptime(m.group(1), "%b").month
            yr = today.year + (1 if mon < today.month - 1 else 0)
            day = date(yr, mon, int(m.group(2)))
            times = re.search(r"eventDoorStartDate.*?<span[^>]*>(.*?)</span>", b, re.S)
            times = clean(times.group(1)) if times else ""
            show = re.search(r"Show:\s*([^A-Za-z]*[ap]\.?m)", times, re.I)
            cost = re.search(r"rhp-event__cost-text[^>]*>(.*?)</span>", b, re.S)
            sub = re.search(r'eventSubHeader[^>]*>(.*?)</h4>', b, re.S)
            age = re.search(r'eventAgeRestriction[^>]*>(.*?)</div>', b, re.S)
            img = re.search(r'<img src="([^"]+)"', b)
            href = link.group(1) if link else None
            name = clean(title.group(1))
            venue = "Catalyst Atrium" if (href and "atrium" in href) or name.lower().startswith("live in the atrium") else "Catalyst Main Room"
            elsewhere = re.search(r"\*+\s*at ([^*]+?)\s*\*+", name, re.I)  # e.g. "Band **At Felton Music Hall**"
            if elsewhere:
                venue = elsewhere.group(1).strip()
                name = name[:elsewhere.start()].strip()
            name = re.sub(r"^live in the atrium:\s*", "", name, flags=re.I)
            info = " · ".join(x for x in [clean(sub.group(1)) if sub else "", times, clean(age.group(1)) if age else ""] if x)
            out.append(event(venue, name, day,
                             parse_time(show.group(1) if show else times), href,
                             clean(cost.group(1)) if cost else None, info or None,
                             img.group(1) if img else None))
    return out


def moes_alley():
    h = get("https://moesalley.com/calendar")
    nonce = re.search(r"action: 'get_events_for_calendar',\s*nonce: '(\w+)'", h).group(1)
    params = re.search(r"params: '(\{.*?\})'", h).group(1)
    start = date.today()
    end = date(start.year + 1, start.month, 1)
    body = urllib.parse.urlencode({
        "action": "get_events_for_calendar", "nonce": nonce,
        "start": start.isoformat(), "end": end.isoformat(), "params": params,
    }).encode()
    data = json.loads(get("https://moesalley.com/wp-admin/admin-ajax.php", body,
                          {"X-Requested-With": "XMLHttpRequest", "Referer": "https://moesalley.com/calendar"}))
    out = []
    for e in data.get("events", []):
        title = clean(e.get("title"))
        if "two-night pass" in title.lower():
            continue
        img = re.search(r'src="([^"]+)"', e.get("imageUrl") or "")
        slug = re.sub(r"[^\w-]+", "", title.lower().replace(" ", "-"))
        doors = e.get("doors")
        out.append(event("Moe's Alley", title, e["start"], parse_time(e.get("displayTime")),
                         "https://moesalley.com/calendar",
                         None, f"Doors {doors}" if doors else None, img.group(1) if img else None))
    return out


def squarespace(venue, base, path, tidy=None):
    data = json.loads(get(f"{base}{path}?format=json"))
    out = []
    for e in data.get("upcoming", []):
        dt = datetime.fromtimestamp(e["startDate"] / 1000, timezone.utc).astimezone(TZ)
        title = clean(e.get("title"))
        price = None
        if tidy:
            title, price = tidy(title)
        excerpt = e.get("excerpt") or ""
        tix = re.search(r'href="(https?://[^"]*(?:ticket|tix)[^"]*)"', excerpt, re.I)
        info = clean(excerpt)
        out.append(event(venue, title, dt.date(), dt.strftime("%H:%M"),
                         tix.group(1) if tix else base + e.get("fullUrl", ""), price,
                         None if info.upper() in ("", "TICKETS") else info,
                         e.get("assetUrl")))
    return out


DAYS = r"(MON|TUES|WEDNES|THURS|FRI|SATUR|SUN)DAY"


def tidy_crepe(title):
    """'BAND, OPENER, WEDNESDAY SEPTEMBER 30 @8PM, $15' -> ('Band, Opener', '$15')."""
    price = re.search(r"(\$\d+(?:\.\d\d)?(?:\s*-\s*\$?\d+)?|FREE)", title, re.I)
    head = re.split(rf",?\s*{DAYS}\b|,?\s*\d{{1,2}}:\d\d\s*[AP]M|,?\s*FROM\s+\d", title, flags=re.I)[0]
    head = head.strip(" ,-!")
    if head.isupper():
        head = head.title().replace("'S ", "'s ")
    return head or title, (price.group(1).upper() if price else None)


def tribe(venue, base, venue_id=None):
    """WordPress sites running The Events Calendar expose /wp-json/tribe/events/v1/events."""
    out, url = [], base + "/wp-json/tribe/events/v1/events?per_page=50" + (f"&venue={venue_id}" if venue_id else "")
    while url:
        body = get(url)
        data = json.loads(body[body.index("{"):])  # some sites print PHP warnings before the JSON
        for e in data.get("events", []):
            dt = datetime.strptime(e["start_date"], "%Y-%m-%d %H:%M:%S")
            img = (e.get("image") or {}).get("url") if isinstance(e.get("image"), dict) else None
            out.append(event(venue, clean(e.get("title")), dt.date(),
                             None if e.get("all_day") else dt.strftime("%H:%M"),
                             e.get("url"), clean(e.get("cost")) or None, None, img))
        url = data.get("next_rest_url")
    return out


def felton():
    h = get("https://www.prekindle.com/events/felton-music-hall")
    m = re.search(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', h, re.S)
    out = []
    for e in json.loads(m.group(1)):
        offers = e.get("offers") or {}
        performers = [p["name"] for p in (e.get("performer") or []) if p.get("name")]
        support = [p for p in performers[1:] if p.lower() != e["name"].lower()]
        low = offers.get("lowPrice")
        out.append(event("Felton Music Hall", clean(e["name"]), e["startDate"][:10],
                         e["startDate"][11:16] if len(e["startDate"]) > 10 else None,
                         (e.get("url") or "").replace("http://", "https://"),
                         f"${float(low):.0f}" if low else None,
                         ("with " + ", ".join(support)) if support else None, e.get("image")))
    return out


# Breweries and bars mix bands with trivia, food pop-ups, etc. Drop the obvious non-music nights.
NOT_MUSIC = re.compile(r"pizza|karaoke|line danc|belly danc|social dance|comedy|trivia|mixer|roll call|"
                       r"keg of honor|bingo|food truck|yoga|run club|market|art night|tie dye|game night|movie|"
                       r"film screening|a film by", re.I)


def music_only(events):
    return [e for e in events if not NOT_MUSIC.search(e["title"])]


def tidy_caps(title):
    """'EL BRICK' -> 'El Brick'; leaves mixed-case titles alone."""
    letters = [c for c in title if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        title = re.sub(r"[A-Za-z]+('[A-Za-z]+)?", lambda m: m.group(0).capitalize(), title.lower())
    return title, None


def tidy_mug(title):
    """'Understory · Sat 10/3, 7-9pm, $30' -> ('Understory', '$30')."""
    head, _, rest = title.partition(" · ")
    price = re.search(r"\$\d+(?:\s*/\s*\$\d+)?", rest)
    if "sold out" in title.lower():
        return head.strip(), "Sold out"
    return head.strip() or title, price.group(0) if price else None


def wix_events(venue, page):
    """Wix Events calendars embed the visible month's events in the page's warmup data."""
    h = get(page)
    warm = json.loads(re.search(r'id="wix-warmup-data">(.*?)</script>', h, re.S).group(1))
    out = []
    for comps in warm.get("appsWarmupData", {}).values():
        for comp in comps.values() if isinstance(comps, dict) else []:
            evs = comp.get("events", {}).get("events") if isinstance(comp, dict) and isinstance(comp.get("events"), dict) else None
            for e in evs or []:
                cfg = e.get("scheduling", {}).get("config", {})
                if not cfg.get("startDate"):
                    continue
                dt = datetime.fromisoformat(cfg["startDate"].replace("Z", "+00:00")).astimezone(TZ)
                slug = e.get("slug")
                out.append(event(venue, clean(e.get("title")), dt.date(), dt.strftime("%H:%M"),
                                 f"{page.rsplit('/', 1)[0]}/event-details-registration/{slug}" if slug else page,
                                 None, clean(e.get("description")) or None))
    return music_only([e for e in out if not re.search(r"closed|ribbon cutting|tasting!", e["title"], re.I)])


def quarry():
    """quarryamphitheater.com lists its shows as schema.org Events (dates like 'Oct 09, 2026')."""
    h = get("https://www.quarryamphitheater.com/")
    out = []
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', h, re.S):
        try:
            e = json.loads(block)
        except ValueError:
            continue
        if "Event" not in str(e.get("@type")) or not e.get("startDate"):
            continue
        try:
            day = datetime.strptime(e["startDate"].strip(), "%b %d, %Y").date()
        except ValueError:
            day = e["startDate"][:10]
        offer = e.get("offers") if isinstance(e.get("offers"), dict) else {}
        out.append(event("Quarry Amphitheater", clean(e.get("name")), day, None,
                         offer.get("url") or "https://www.quarryamphitheater.com/",
                         offer.get("price") or None, None, e.get("image")))
    return music_only(out)


def discretion():
    h = get("https://www.discretionbrewing.com/events/")
    out = []
    for cls, body in re.findall(r'<div class="col-md-4 loop-item([^"]*)">(.*?)<a href="#loop-item-detail', h, re.S):
        if "live-music" not in cls:
            continue
        title = re.search(r"<h3>(.*?)</h3>", body, re.S)
        d = re.search(r'loop-item-sub">[^<\d]*(\d{1,2})/(\d{1,2})/(\d{2})', body)
        if not (title and d):
            continue
        day = date(2000 + int(d.group(3)), int(d.group(1)), int(d.group(2)))
        excerpt = clean(re.search(r'loop-item-excerpt">(.*?)</p>\s*</p>', body, re.S).group(1)) if "loop-item-excerpt" in body else ""
        out.append(event("Discretion Brewing", clean(title.group(1)), day, parse_time(excerpt),
                         "https://www.discretionbrewing.com/events/", None, excerpt or None))
    return out


def mission_west():
    h = get("https://missionwestbar.com/santa-cruz-santa-cruz-westside-mission-west-bar-events")
    out = []
    for start, title in re.findall(r'<var class="atc_date_start">([^<]+)</var>.*?<var class="atc_title">([^<]*)</var>', h, re.S):
        dt = datetime.strptime(start.strip(), "%Y-%m-%d %H:%M:%S")
        out.append(event("Mission West", tidy_caps(clean(title))[0], dt.date(),
                         dt.strftime("%H:%M") if dt.hour else None,
                         "https://missionwestbar.com/santa-cruz-santa-cruz-westside-mission-west-bar-events"))
    return music_only(out)


def localgroove(venue, path, label=None):
    """LocalGroove venue pages carry schema.org MusicEvent JSON-LD."""
    h = get("https://www.localgroove.live" + path)
    out = []
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', h, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for e in data if isinstance(data, list) else [data]:
            if "Event" not in str(e.get("@type")) or not e.get("startDate"):
                continue
            title = re.sub(r"\s+at\s+" + re.escape(venue) + r"\s*$", "", clean(e.get("name")), flags=re.I)
            s = e["startDate"]
            out.append(event(label or venue, title, s[:10], s[11:16] if len(s) > 10 else None, e.get("url")))
    return music_only(out)


def el_vaquero():
    out = []
    for e in json.loads(get("https://reservations.elvaquerowinery.com/api/events")):
        cents = e.get("ticket_price_cents")
        info = " · ".join(x for x in [e.get("genre"), f"Food: {e['food_vendor']}" if (e.get("food_vendor") or "TBD").strip().upper() != "TBD" else ""] if x)
        out.append(event("El Vaquero Winery", clean(e.get("title")), e["date"][:10], e.get("start_time") or None,
                         f"https://reservations.elvaquerowinery.com/events/{e['_id']}",
                         f"${int(cents) / 100:.0f}" if cents and cents != "0" else None, info or None,
                         e.get("image_url")))
    return music_only(out)


def _repeats(start, rule, horizon):
    """Expand a Boom Calendar repeat rule into dates (start included)."""
    kind, step = rule.get("type"), int(rule.get("interval") or 1)
    if not kind:
        return [start]
    end = str(rule.get("end") or "")
    until, count = (date.fromisoformat(end[:10]), None) if "-" in end else (horizon, int(end or 0) or None)
    until = min(until, horizon)
    nth = (start.day - 1) // 7  # e.g. 3rd Saturday -> 2
    by_weekday = str(rule.get("advanced")).strip("[]'\" ") == "1"
    out, i = [], 0
    while True:
        if kind == "Week":
            d = start + timedelta(weeks=i * step)
        else:
            y, m = divmod(start.month - 1 + i * step, 12)
            y, m = start.year + y, m + 1
            if by_weekday:
                first = date(y, m, 1)
                d = first + timedelta(days=(start.weekday() - first.weekday()) % 7 + 7 * nth)
                if d.month != m:
                    i += 1
                    continue
            else:
                try:
                    d = date(y, m, start.day)
                except ValueError:
                    i += 1
                    continue
        if d > until or (count and i >= count):
            break
        out.append(d)
        i += 1
    skip = set(rule.get("exclude") or [])
    extra = [date.fromisoformat(x[:10]) for x in rule.get("additionalDates") or []]
    return [d for d in out if d.isoformat() not in skip] + extra


def shanty_shack():
    """Their Wix site embeds a Boom Calendar; its feed needs a per-visit Wix access token."""
    get("https://www.shantyshackbrewing.com/events")
    tokens = json.loads(get("https://www.shantyshackbrewing.com/_api/v1/access-tokens"))
    instance = tokens["apps"]["13b4a028-00fa-7133-242f-4628106b8c91"]["instance"]
    q = urllib.parse.urlencode({"comp_id": "comp-l323yc00", "instance": instance,
                                "originCompId": "", "time_zone": "America/Los_Angeles"})
    cal = json.loads(get("https://calendar.apiboomtech.com/api/published_calendar?" + q,
                         headers={"Origin": "https://calendar.boomte.ch", "Referer": "https://calendar.boomte.ch/"}))
    today = date.today()
    horizon = date(today.year + 1, today.month, 1)
    out = []
    for e in cal.get("events", []):
        start = datetime.fromisoformat(e["start"]) if "T" in e["start"] else datetime.fromisoformat(e["start"] + "T00:00")
        rule = e.get("repeat") if isinstance(e.get("repeat"), dict) else {}
        link = e.get("link") if str(e.get("link")).startswith("http") else "https://www.shantyshackbrewing.com/events"
        info = clean(e.get("desc"))
        for d in _repeats(start.date(), rule, horizon):
            if d >= today:
                out.append(event("Shanty Shack Brewing", clean(e.get("title")), d,
                                 None if e.get("all_day") == "1" or "T" not in e["start"] else start.strftime("%H:%M"),
                                 link, None, info or None, e.get("image")))
    return music_only(out)


def shanty_shack_with_fallback():
    try:
        return shanty_shack()
    except Exception as ex:
        print(f"  Shanty Shack calendar failed ({ex}); using LocalGroove", file=sys.stderr)
        return localgroove("Shanty Shack Brewing", "/santa-cruz/venue/shanty-shack-brewing")


VENUES = {
    "Catalyst": catalyst,
    "Moe's Alley": moes_alley,
    "Rio Theatre": lambda: squarespace("Rio Theatre", "https://www.riotheatre.com", "/events-2"),
    "The Crepe Place": lambda: squarespace("The Crepe Place", "https://www.thecrepeplace.com", "/shows-list", tidy_crepe),
    "Kuumbwa Jazz": lambda: tribe("Kuumbwa Jazz", "https://www.kuumbwajazz.org"),
    "Cat Alley Street": lambda: tribe("Cat Alley Street", "https://catalleystreet.com"),
    "Felton Music Hall": felton,
    "Woodhouse": lambda: music_only(squarespace("Woodhouse", "https://www.woodhousebrews.com", "/events")),
    "Abbott Square": lambda: music_only(squarespace("Abbott Square", "https://abbottsquaremarket.com", "/events")),
    "Discretion Brewing": discretion,
    "Mission West": mission_west,
    "El Vaquero Winery": el_vaquero,
    "Shanty Shack": shanty_shack_with_fallback,
    "UCSC Recital Hall": lambda: tribe("UCSC Recital Hall", "https://events.ucsc.edu", 108),
    "Quarry Amphitheater": quarry,
    "Henfling's Tavern": lambda: localgroove("Henflings Tavern", "/ben-lomond/venue/henflings-tavern", "Henfling's Tavern"),
    "Ugly Mug": lambda: music_only(squarespace("Ugly Mug", "https://www.cafeugly.com", "/live-music-the-mug", tidy_mug)),
    "The Sand Bar": lambda: tribe("The Sand Bar", "https://thesandbarcapitola.com"),
    "Cork & Fork": lambda: wix_events("Cork & Fork", "https://www.corkandforkcapitola.com/music-events"),
}


# ---------- calendar feeds (.ics) ----------

VTIMEZONE = """BEGIN:VTIMEZONE
TZID:America/Los_Angeles
BEGIN:DAYLIGHT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
TZNAME:PDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
TZNAME:PST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")


def _ics_text(s):
    return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    """RFC 5545: lines over 75 bytes continue on the next line after a space."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode()) > 74:
            out.append(cur)
            cur = " "
        cur += ch
    return "\r\n".join(out + [cur])


def write_ics(events, path, name):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Santa Cruz Shows//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_ics_text(name)}", "X-WR-TIMEZONE:America/Los_Angeles",
             "REFRESH-INTERVAL;VALUE=DURATION:PT12H", "X-PUBLISHED-TTL:PT12H"] + VTIMEZONE.splitlines()
    for e in events:
        uid = hashlib.sha1(f"{e['venue']}|{e['date']}|{e['title']}".encode()).hexdigest()[:20]
        day = date.fromisoformat(e["date"])
        lines += ["BEGIN:VEVENT", f"UID:{uid}@santa-cruz-shows", f"DTSTAMP:{stamp}"]
        if e.get("time"):
            start = datetime.combine(day, datetime.strptime(e["time"], "%H:%M").time())
            end = start + timedelta(hours=3)
            lines += [f"DTSTART;TZID=America/Los_Angeles:{start:%Y%m%dT%H%M%S}",
                      f"DTEND;TZID=America/Los_Angeles:{end:%Y%m%dT%H%M%S}"]
        else:
            lines += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}"]
        desc = "\n".join(x for x in [e.get("price"), e.get("info"), e.get("url")] if x)
        lines += [f"SUMMARY:{_ics_text(e['title'] + ' @ ' + e['venue'])}", f"LOCATION:{_ics_text(e['venue'])}"]
        if desc:
            lines.append(f"DESCRIPTION:{_ics_text(desc)}")
        if e.get("url"):
            lines.append(f"URL:{e['url']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    with open(path, "w", newline="") as f:
        f.write("\r\n".join(_fold(l) for l in lines) + "\r\n")


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "events.json"
    today = date.today().isoformat()
    events, sources = [], []
    for name, fn in VENUES.items():
        try:
            got = [e for e in fn() if e["date"] >= today]
            events += got
            sources.append({"venue": name, "ok": True, "count": len(got)})
            print(f"  {name:<20} {len(got):>3} events", file=sys.stderr)
        except Exception as ex:  # keep going if one site changes
            sources.append({"venue": name, "ok": False, "error": str(ex)[:200]})
            print(f"  {name:<20} FAILED: {ex}", file=sys.stderr)
    seen, uniq = set(), []
    for e in sorted(events, key=lambda e: (e["date"], e["time"] or "99", e["venue"])):
        key = (e["venue"], e["date"], e["title"].lower())
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    doc = {"updated": datetime.now(TZ).isoformat(timespec="minutes"), "sources": sources, "events": uniq}
    with open(out_path, "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    print(f"Wrote {len(uniq)} events to {out_path}", file=sys.stderr)

    # Subscribable calendars: everything, plus one per venue.
    base = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(os.path.join(base, "ics"), exist_ok=True)
    write_ics(uniq, os.path.join(base, "events.ics"), "Santa Cruz Shows")
    for v in sorted({e["venue"] for e in uniq}):
        write_ics([e for e in uniq if e["venue"] == v], os.path.join(base, "ics", slugify(v) + ".ics"), f"{v} (Santa Cruz Shows)")
    print(f"Wrote events.ics and {len({e['venue'] for e in uniq})} venue calendars", file=sys.stderr)


if __name__ == "__main__":
    main()
