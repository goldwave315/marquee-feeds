"""Marquee feeds: scrape Portland venue calendars into one JSON list.

Runs on GitHub Actions every few hours. Writes:
  data/events.json  every upcoming show we can see, with when we first saw it
  data/new.json     shows first seen in the last 14 days (newest first)
  data/new.xml      the same as an RSS feed (for a feed reader)
  data/seen.json    id -> first-seen timestamp, kept so a show that drops off
                    a page for a day is not reported as new again

Only public venue listings live here. No personal data.
"""

import html
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

PT = ZoneInfo("America/Los_Angeles")
NOW = datetime.now(timezone.utc)
TODAY = NOW.astimezone(PT).date()
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
NEW_WINDOW_DAYS = 14

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 marquee-feeds"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

session = requests.Session()
session.headers.update(HEADERS)


def get(url, **kw):
    r = session.get(url, timeout=30, **kw)
    r.raise_for_status()
    return r


def clean(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", html.unescape(str(text))).strip()


def pt_date_from_iso(value):
    """ISO timestamp (any offset, or Z) -> Pacific calendar date string."""
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    v = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v)  # -0700 -> -07:00
    v = re.sub(r"([+-])(\d):(\d{2})$", r"\g<1>0\2:\3", v)  # -8:00 -> -08:00 (Laurelthirst)
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.date().isoformat()
    return dt.astimezone(PT).date().isoformat()


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_loose_date(text):
    """'November 27, 2026', 'Fri, November 27th, 2026', 'Sat Nov 21', '11.24' -> ISO date.

    A date with no year is the next time that month and day comes round
    (allowing two days of grace for tonight's late listings). Numeric dates are
    read US-style, month first (Dante's lists '11.24').
    """
    if not text:
        return None
    t = clean(text).lower()
    month = day = year = None
    for m in re.finditer(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?", t):
        if m.group(1) in MONTHS:
            month, day = MONTHS[m.group(1)], int(m.group(2))
            year = int(m.group(3)) if m.group(3) else None
            break
    if month is None:
        n = re.search(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2}|\d{4}))?\b", t)
        if not n:
            return None
        month, day = int(n.group(1)), int(n.group(2))
        if n.group(3):
            year = int(n.group(3)) + (2000 if len(n.group(3)) == 2 else 0)
    if year:
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    # If the text names a weekday ('Wednesday, November 3'), use it to pick the year:
    # a stale listing from earlier this year must not be read as next year's show.
    wd = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                   r"mon|tues?|wed|thur?s?|fri|sat|sun)\b", t)
    weekday = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(wd.group(1)[:3]) if wd else None
    candidates = []
    for year in (TODAY.year, TODAY.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d >= TODAY - timedelta(days=2):
            candidates.append(d)
    for d in candidates:
        if weekday is None or d.weekday() == weekday:
            return d.isoformat()
    # The weekday matched neither year (a typo on the page): fall back to the next date.
    return candidates[0].isoformat() if candidates else None


def unique(events):
    """Drop repeats of the same id within one source (pages that render a list twice)."""
    out, seen = [], {}
    for e in events:
        if e["id"] in seen:
            if not out[seen[e["id"]]]["title"] and e["title"]:
                out[seen[e["id"]]] = e
            continue
        seen[e["id"]] = len(out)
        out.append(e)
    return out


def event(source, native_id, venue, title, day, url, support="", status="", announced=None,
          onsale=None):
    return {
        "id": f"{source}:{native_id}",
        "venue": clean(venue).strip(" -–—"),
        "title": clean(title),
        "support": clean(support) if support and clean(support).lower() != "null" else "",
        "date": day,
        "url": url,
        "status": status or "",
        "announced": announced,
        "onsale": onsale,
        "source": source,
    }


# ---------------------------------------------------------------- parsers

TRUEWEST_SLUGS = {
    "mississippi-studios": "Mississippi Studios",
    "polaris-hall": "Polaris Hall",
    "revolution-hall": "Revolution Hall",
    "show-bar-at-revolution-hall": "Show Bar",
    "aladdin-theater": "Aladdin Theater",
}


def truewest(base, default_venue):
    """Mississippi Studios, Revolution Hall, Aladdin: the wlcr events endpoint.

    Each page returns an HTML fragment of schema.org Event blocks. Every page
    repeats the three 'just announced' shows, so we stop when a page adds no
    new event ids.
    """
    home = get(base + "/").text
    m = re.search(r"wp-json/wp/v2/pages/(\d+)", home)
    page_id = m.group(1) if m else "6"
    out, seen = [], set()
    for page in range(1, 40):
        r = get(f"{base}/wp-json/wlcr/v1/events/all/page/{page}",
                params={"style": "default", "page_id": page_id})
        try:
            fragment = r.json()
        except ValueError:
            fragment = r.text
        if not isinstance(fragment, str):
            fragment = json.dumps(fragment)
        soup = BeautifulSoup(fragment, "html.parser")
        added = 0
        for el in soup.select("[data-event-id]"):
            eid = el.get("data-event-id")
            if not eid or eid in seen:
                continue
            seen.add(eid)
            added += 1
            name = el.select_one("[itemprop=name]")
            title = name.get_text(" ") if name else ""
            sup = el.select_one("h4, .event-supporting")
            day = None
            meta = el.select_one("meta[itemprop=startDate]")
            if meta and meta.get("content"):
                day = meta["content"][:10]
            if not day:
                doors = el.select_one("[data-event-doors]")
                if doors:
                    day = pt_date_from_iso(doors.get("data-event-doors"))
            if not day:
                full = el.select_one(".event-date--full")
                day = parse_loose_date(full.get_text(" ") if full else "")
            link = el.select_one("a[href*='etix.com']")
            url = link["href"] if link else base
            venue = ""
            v = el.select_one(".event-venue")
            if v:
                venue = v.get_text(" ")
            if not venue and link:
                slug = link["href"].rstrip("/").split("-portland-")[-1]
                venue = TRUEWEST_SLUGS.get(slug, default_venue)
            status = ""
            btn = el.select_one("[class*='event-status--']")
            if btn:
                sm = re.search(r"event-status--([a-z-]+)", " ".join(btn.get("class", [])))
                status = sm.group(1) if sm else ""
            out.append(event("etix", eid, venue or default_venue, title, day, url,
                             support=sup.get_text(" ") if sup else "", status=status))
        if added == 0:
            break
        time.sleep(0.5)
    return out


def ticketweb_wp(base, venue):
    """Star Theater, Jack London Revue: TicketWeb WordPress plugin, /page/N/."""
    out, seen = [], set()
    for page in range(1, 12):
        url = base + "/" if page == 1 else f"{base}/page/{page}/"
        try:
            text = get(url).text
        except requests.HTTPError:
            break
        soup = BeautifulSoup(text, "html.parser")
        added = 0
        for sec in soup.select(".tw-section"):
            tix = sec.select_one("a[href*='ticketweb.com/event/']")
            if not tix:
                continue
            m = re.search(r"/(\d+)(?:[/?#]|$)", tix["href"])
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            added += 1
            name = sec.select_one(".tw-name a") or sec.select_one(".tw-name")
            prefix = sec.select_one(".tw-prefix")
            d = sec.select_one(".tw-event-date")
            href = name.get("href") if name and name.name == "a" else tix["href"]
            out.append(event("ticketweb", m.group(1), venue,
                             name.get_text(" ") if name else "",
                             parse_loose_date(d.get_text(" ") if d else ""), href,
                             support=prefix.get_text(" ") if prefix else ""))
        if added == 0:
            break
        time.sleep(0.5)
    return out


def holocene():
    data = get("https://www.holocene.org/api/events").json()
    out = []
    for e in data:
        url = e.get("url") or e.get("ticketUrl") or "https://www.holocene.org/calendar"
        out.append(event("holocene", e.get("id"), "Holocene",
                         e.get("displayTitle") or e.get("name"),
                         e.get("dayKey") or pt_date_from_iso(e.get("dateTime")), url,
                         support=e.get("support") or e.get("subtitle") or "",
                         status=(json.loads(e["availability"]).get("status")
                                 if isinstance(e.get("availability"), str) and
                                 e["availability"].startswith("{") else ""),
                         announced=e.get("announcedDate"), onsale=e.get("onsaleDate")))
    return out


def tribe(base, venue, source, skip=None):
    """The Events Calendar REST API (Pioneer Courthouse Square, Edgefield, Crystal Ballroom).

    skip: a regex of titles that are not shows (building tours, brewfests).
    """
    out = []
    for page in range(1, 20):
        r = session.get(f"{base}/wp-json/tribe/events/v1/events",
                        params={"per_page": 50, "page": page,
                                "start_date": TODAY.isoformat()}, timeout=30)
        if r.status_code == 404 or r.status_code == 400:
            break
        r.raise_for_status()
        j = r.json()
        for e in j.get("events", []):
            if skip and skip.search(clean(e.get("title"))):
                continue
            out.append(event(source, e.get("id"), venue, e.get("title"),
                             (e.get("start_date") or "")[:10] or None, e.get("url"),
                             announced=e.get("date")))
        if page >= int(j.get("total_pages") or 1):
            break
    return out


def jsonld(url, venue, source):
    """Pages that embed schema.org Event objects (The Get Down)."""
    soup = BeautifulSoup(get(url).text, "html.parser")
    out = []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            t = o.get("@type")
            types = t if isinstance(t, list) else [t]
            if any(isinstance(x, str) and x.endswith("Event") for x in types):
                link = o.get("url")
                offers = o.get("offers")
                if not link and isinstance(offers, dict):
                    link = offers.get("url")
                if not link and isinstance(offers, list) and offers:
                    link = offers[0].get("url")
                native = link or f"{o.get('name')}|{o.get('startDate')}"
                out.append(event(source, native, venue, o.get("name"),
                                 pt_date_from_iso(o.get("startDate")), link or url))
            for k in ("@graph", "itemListElement", "item"):
                if k in o:
                    walk(o[k])

    for s in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(s.string or s.get_text() or "null"))
        except (ValueError, TypeError):
            continue
    return out


def wonder(previous):
    """Wonder Ballroom: events RSS, newest announcements first.

    The RSS has no show date, so the first time we see a show we read its
    page title ('Artist I Official Info I May 10, 2027 I Wonder Ballroom').
    """
    text = get("https://wonderballroom.com/events/feed/").text
    soup = BeautifulSoup(text, "xml")
    out = []
    for item in soup.find_all("item"):
        link = clean(item.link.get_text()) if item.link else ""
        slug = link.rstrip("/").split("/event/")[-1].split("/")[0] if "/event/" in link else link
        eid = f"wonder:{slug}"
        pub = item.pubDate.get_text() if item.pubDate else None
        announced = None
        if pub:
            try:
                announced = parsedate_to_datetime(pub).astimezone(PT).isoformat()
            except (TypeError, ValueError):
                announced = None
        day = previous.get(eid, {}).get("date")
        if not day and link:
            try:
                page = BeautifulSoup(get(link).text, "html.parser")
                # schema.org Event on the page: "startDate": "2027-04-03T20:00:00-0700"
                for s in page.find_all("script", type="application/ld+json"):
                    m = re.search(r'"startDate"\s*:\s*"(\d{4}-\d{2}-\d{2})', s.get_text())
                    if m:
                        day = m.group(1)
                        break
                if not day:
                    title_tag = page.title.get_text() if page.title else ""
                    m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", title_tag)
                    day = parse_loose_date(m.group(1)) if m else None
                time.sleep(0.5)
            except requests.RequestException:
                day = None
        out.append(event("wonder", slug, "Wonder Ballroom", item.title.get_text() if item.title else "",
                         day, link, announced=announced))
    return out


def etix_id(href):
    m = re.search(r"etix\.com/ticket/[a-z]/(\d+)", href or "")
    return m.group(1) if m else None


def rhp(url, default_venue):
    """Rockhouse Partners (RHP) WordPress calendars: Roseland, Hawthorne, Alberta Rose.

    One server-rendered page lists about six months of shows. Single shows sit
    in .rhpSingleEvent blocks; multi-night runs sit in .rhpEventSeries with one
    <li> per night. Shows sold through Etix keep the etix:<id> id the True West
    sites use, so a show listed on both is kept once.
    """
    soup = BeautifulSoup(get(url).text, "html.parser")
    out = []

    def status_of(el):
        st = ""
        for sp in el.select("span.rhp-event-cta"):  # nested spans: the innermost wins
            for c in sp.get("class", []):
                if c != "rhp-event-cta":
                    st = c
        return st

    for ev in soup.select(".rhpSingleEvent"):
        t = ev.select_one(".rhp-event__title--list") or ev.select_one("#eventTitle")
        title = t.get_text(" ") if t else ""
        d = ev.select_one(".singleEventDate")
        day = parse_loose_date(d.get_text(" ") if d else "")
        sub = ev.select_one(".eventSubHeader")
        support = re.sub(r"^\s*(with|w/)\s+", "", clean(sub.get_text(" ")), flags=re.I) if sub else ""
        v = ev.select_one(".venueLink")
        venue = v.get_text(" ") if v else default_venue
        tix = ev.select_one("a[href*='etix.com']")
        page = ev.select_one("a.url")
        eid = etix_id(tix["href"]) if tix else None
        link = tix["href"].split("?")[0] if tix else (page["href"] if page else url)
        native = eid or (page["href"] if page else f"{title}|{day}")
        out.append(event("etix" if eid else "rhp", native, venue, title, day, link,
                         support=support, status=status_of(ev)))
    for ser in soup.select(".rhpEventSeries"):
        t = ser.select_one(".rhp-event__title--list")
        title = t.get_text(" ") if t else ""
        for li in ser.select(".rhp-event-series-individual"):
            d = li.select_one(".rhp-event-series-date")
            day = parse_loose_date(d.get_text(" ") if d else "")
            tix = li.select_one("a[href*='etix.com']")
            eid = etix_id(tix["href"]) if tix else None
            out.append(event("etix" if eid else "rhp", eid or f"{title}|{day}", default_venue, title,
                             day, tix["href"].split("?")[0] if tix else url, status=status_of(li)))
    return unique(out)


def opendate(url, venue):
    """Opendate venue pages (Turn! Turn! Turn!): one server-rendered card per show."""
    soup = BeautifulSoup(get(url).text, "html.parser")
    out = []
    for card in soup.select(".confirm-card"):
        a = card.select_one("a.public-listing-text") or card.select_one("a[href]")
        if not a:
            continue
        href = a["href"].split("?")[0]
        m = re.search(r"-(\d+)$", href)
        dates = card.select(".public-listing-date")
        day = parse_loose_date(dates[0].get_text(" ")) if dates else None
        out.append(event("opendate", m.group(1) if m else href, venue, a.get_text(" "), day, href))
    return out


MCM_SKIP = re.compile(r"history (&|and) art tour|brewfest|bazaar|thanksgiving|brunch|"
                      r"birthday, terminator stout|friendsgiving", re.I)


def mcmenamins(url, rooms):
    """McMenamins property pages (White Eagle, Mission Theater): event cards.

    rooms maps the room name printed on the card to the venue name we use;
    cards for other rooms and chain-wide promotions ('All Locations') are skipped.
    Card titles are lines split by <br>: a 'X Presents' line is the presenter,
    the next line is the headliner, the rest are support acts.
    """
    soup = BeautifulSoup(get(url).text, "html.parser")
    out = []
    for card in soup.select(".tm-panel-card.event.detailed"):
        if "movie" in card.get("class", []):
            continue
        room = card.select_one("h4.tm-card-title span")
        venue = rooms.get(clean(room.get_text(" "))) if room else None
        if not venue:
            continue
        head = card.select_one(".tm-panel-titlebg h3")
        if not head:
            continue
        lines = [clean(x) for x in head.get_text("\n").split("\n") if clean(x)]
        lines = [x for x in lines if not re.search(r"\bpresents?$", x, re.I)] or lines
        title = lines[0]
        if MCM_SKIP.search(" ".join(lines)):
            continue
        when = card.select_one(".tm-card-content h3")
        day = parse_loose_date(when.get_text(" ") if when else "")
        link = card.select_one("a[href*='/events/']")
        href = link["href"] if link else url
        m = re.search(r"/events/(\d+)", href)
        out.append(event("mcmenamins", m.group(1) if m else f"{title}|{day}", venue, title, day,
                         "https://www.mcmenamins.com" + href if href.startswith("/") else href,
                         support=", ".join(lines[1:4])))
    return unique(out)


def squarespace_events(url, venue, source):
    """Squarespace event collections read as JSON (Alberta Street Pub, Swan Dive)."""
    j = get(url, params={"format": "json"}).json()
    base = re.match(r"https?://[^/]+", url).group(0)
    out = []
    for it in j.get("upcoming") or j.get("items") or []:
        start = it.get("startDate")
        day = None
        if isinstance(start, (int, float)):
            day = datetime.fromtimestamp(start / 1000, timezone.utc).astimezone(PT).date().isoformat()
        link = base + it["fullUrl"] if it.get("fullUrl") else url
        out.append(event(source, it.get("id") or link, venue, it.get("title"), day, link))
    return out


SONGKICK_ICS = os.environ.get(
    "SONGKICK_ICS",
    "https://www.songkick.com/users/todayagain/calendars.ics?filter=tracked_artist")
# Songkick lists every show by a tracked artist across the region; keep the ones a
# Portland night out can reach.
SONGKICK_PLACES = re.compile(
    r", (Portland|Troutdale|Beaverton|Hillsboro|Gresham|Lake Oswego|Happy Valley|Milwaukie|"
    r"Oregon City|Tigard), OR, US$|, (Ridgefield|Vancouver), WA, US$")


def songkick():
    """Aniss's Songkick calendar: shows by artists he tracks there (iCalendar)."""
    # Songkick answers 406 to some header sets, so try a calendar client first,
    # then a bare request, then the browser-style session.
    attempts = [
        {"User-Agent": "Mozilla/5.0 (compatible; iCal/4.0)", "Accept": "text/calendar, */*"},
        {"User-Agent": "curl/8.5.0", "Accept": "*/*"},
        None,
    ]
    text, last = None, None
    for hdrs in attempts:
        try:
            r = (requests.get(SONGKICK_ICS, headers=hdrs, timeout=30) if hdrs
                 else session.get(SONGKICK_ICS, timeout=30))
            r.raise_for_status()
            if "BEGIN:VCALENDAR" in r.text:
                text = r.text
                break
            last = ValueError("response was not a calendar")
        except requests.RequestException as exc:
            last = exc
    if text is None:
        raise last
    text = re.sub(r"\r?\n[ \t]", "", text)          # unfold continuation lines
    out = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        props = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            props[key.split(";")[0]] = (val.replace("\\,", ",").replace("\\;", ";")
                                        .replace("\\n", " ").strip())
        loc = props.get("LOCATION", "")
        if not SONGKICK_PLACES.search(loc):
            continue
        uid = props.get("UID", "").split("@")[0]
        m = re.match(r"(\d{4})(\d{2})(\d{2})", props.get("DTSTART", ""))
        day = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
        summary = re.sub(r"\s*\(\d{1,2} \w{3} \d{2}\)\s*$", "", props.get("SUMMARY", ""))
        venue = loc.split(",")[0].strip()
        title = summary
        if venue and summary.endswith(" at " + venue):
            title = summary[: -len(" at " + venue)]
        elif " at " in summary:
            title = summary.rsplit(" at ", 1)[0]
        url = (props.get("URL") or props.get("DESCRIPTION") or "").split("?")[0]
        e = event("songkick", uid or f"{title}|{day}", venue or "Unknown venue", title, day, url)
        e["tracked_artist"] = True
        e["city"] = loc.split(",")[-3].strip() if loc.count(",") >= 3 else ""
        out.append(e)
    return out


def site_up(url):
    try:
        r = session.get(url, timeout=20)
        return r.status_code < 400
    except requests.RequestException:
        return False


# ---------------------------------------------------------------- sources

SOURCES = [
    ("Mississippi Studios + Polaris Hall",
     lambda prev: truewest("https://mississippistudios.com", "Mississippi Studios")),
    ("Revolution Hall + Show Bar",
     lambda prev: truewest("https://www.revolutionhall.com", "Revolution Hall")),
    ("Aladdin Theater",
     lambda prev: truewest("https://www.aladdin-theater.com", "Aladdin Theater")),
    ("Star Theater", lambda prev: ticketweb_wp("https://www.startheaterportland.com", "Star Theater")),
    ("Jack London Revue", lambda prev: ticketweb_wp("https://www.jacklondonrevue.com", "Jack London Revue")),
    ("The Get Down", lambda prev: jsonld("https://thegetdownpdx.com/", "The Get Down", "getdown")),
    ("Wonder Ballroom", wonder),
    ("Holocene", lambda prev: holocene()),
    ("Pioneer Courthouse Square",
     lambda prev: tribe("https://www.thesquarepdx.org", "Pioneer Courthouse Square", "square")),
    ("Edgefield", lambda prev: tribe("https://edgefieldconcerts.com", "McMenamins Edgefield", "edgefield")),
    # Added Nov 2026: the rooms that used to reach Marquee only through DoPDX or Songkick.
    ("Crystal Ballroom",
     lambda prev: tribe("https://www.crystalballroompdx.com", "Crystal Ballroom", "crystal",
                        skip=re.compile(r"history (&|and) art tour|brewfest|bazaar", re.I))),
    ("Roseland Theater", lambda prev: rhp("https://roselandpdx.com/events/", "Roseland Theater")),
    ("Hawthorne Theatre", lambda prev: rhp("https://hawthornetheatre.com/events/", "Hawthorne Theatre")),
    ("Alberta Rose Theatre",
     lambda prev: rhp("https://albertarosetheatre.com/calendar/", "Alberta Rose Theatre")),
    ("Dante's", lambda prev: ticketweb_wp("https://www.danteslive.com", "Dante's")),
    ("Turn! Turn! Turn!",
     lambda prev: opendate("https://app.opendate.io/v/turn-turn-turn-3116", "Turn! Turn! Turn!")),
    ("White Eagle Saloon",
     lambda prev: mcmenamins("https://www.mcmenamins.com/white-eagle-saloon-hotel/white-eagle-saloon",
                             {"White Eagle Saloon": "White Eagle Saloon"})),
    ("Mission Theater",
     lambda prev: mcmenamins("https://www.mcmenamins.com/mission-theater",
                             {"Mission Theater": "Mission Theater"})),
    ("The Showdown", lambda prev: ticketweb_wp("https://www.showdownpdx.com", "The Showdown")),
    ("Laurelthirst Public House",
     lambda prev: jsonld("https://laurelthirst.com/", "Laurelthirst Public House", "laurelthirst")),
    ("Alberta Street Pub",
     lambda prev: squarespace_events("https://www.albertastreetpub.com/music", "Alberta Street Pub", "asp")),
    ("Swan Dive",
     lambda prev: squarespace_events("https://www.swandivepdx.com/events-2-1", "Swan Dive", "swandive")),
    ("Songkick tracked artists", lambda prev: songkick()),
]

WATCH_SITES = {
    # Doug Fir is closed and reopening at 301 SE Morrison (expected early 2027).
    "Doug Fir Lounge website": "https://www.dougfirlounge.com/",
}


# ---------------------------------------------------------------- main

def load(name, default):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(name, obj):
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")


def rss(items):
    from xml.sax.saxutils import escape
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        "<title>Marquee: newly announced Portland shows</title>",
        "<link>https://github.com/goldwave315/marquee-feeds</link>",
        "<description>Shows first seen on Portland venue calendars in the last two weeks.</description>",
    ]
    for e in items[:200]:
        when = e["date"] or "date TBA"
        title = f"{e['title']} at {e['venue']}, {when}"
        desc = " | ".join(x for x in [e.get("support"), e.get("status")] if x)
        pub = format_datetime(datetime.fromisoformat(e["first_seen"]))
        parts.append(
            "<item>"
            f"<title>{escape(title)}</title>"
            f"<link>{escape(e['url'] or '')}</link>"
            f"<guid isPermaLink=\"false\">{escape(e['id'])}</guid>"
            f"<pubDate>{pub}</pubDate>"
            f"<description>{escape(desc)}</description>"
            "</item>")
    parts.append("</channel></rss>")
    return "\n".join(parts) + "\n"


def is_sold_out(status):
    s = status.lower().replace("_", "-").replace(" ", "-")
    return "sold-out" in s or "soldout" in s


def main():
    os.makedirs(DATA, exist_ok=True)
    prev_doc = load("events.json", {"events": [], "sources": {}})
    prev = {e["id"]: e for e in prev_doc.get("events", [])}
    # seen.json: id -> {"first_seen": iso, "baseline": bool}. A show is baseline when
    # its venue was being read for the first time, so it is not news.
    seen = load("seen.json", {})
    stamp = NOW.isoformat(timespec="seconds")
    known_sources = {n for n, s in prev_doc.get("sources", {}).items()
                     if s.get("ok") and s.get("count")}

    events, sources = [], {}
    for name, fn in SOURCES:
        try:
            got = fn(prev)
            got = [e for e in got if e["title"]]
            sources[name] = {"ok": True, "count": len(got), "checked": stamp}
            if not got:
                sources[name]["note"] = "returned no shows; kept the previous list"
                got = [e for e in prev.values() if e.get("source_name") == name]
        except Exception as exc:  # keep going; one venue failing must not stop the rest
            sources[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300],
                             "checked": stamp}
            got = [e for e in prev.values() if e.get("source_name") == name]
        for e in got:
            e["source_name"] = name
        events.extend(got)
        print(f"{name}: {sources[name]}", file=sys.stderr)

    watch = {label: {"up": site_up(url), "url": url, "checked": stamp}
             for label, url in WATCH_SITES.items()}

    keep, ids = [], set()
    cutoff = (TODAY - timedelta(days=1)).isoformat()
    for e in events:
        if e["id"] in ids:
            continue
        if e.get("date") and e["date"] < cutoff:
            continue
        ids.add(e["id"])
        if e["id"] not in seen:
            seen[e["id"]] = {"first_seen": stamp,
                             "baseline": e["source_name"] not in known_sources}
        rec = seen[e["id"]]
        e["first_seen"] = rec["first_seen"]
        e["baseline"] = rec["baseline"]
        # Sell-out tracking: remember each status change, and the first time a show
        # reads as sold out. sold_out_baseline means it was already sold out the
        # first time we saw it, so the true sell-out date is earlier and unknown.
        st = (e.get("status") or "").lower()
        if st:
            hist = rec.setdefault("status_history", [])
            if not hist or hist[-1][1] != st:
                hist.append([stamp, st])
                del hist[:-8]
            if is_sold_out(st) and "sold_out_seen" not in rec:
                rec["sold_out_seen"] = stamp
                rec["sold_out_baseline"] = len(hist) == 1
        if rec.get("sold_out_seen"):
            e["sold_out_seen"] = rec["sold_out_seen"]
            e["sold_out_baseline"] = rec.get("sold_out_baseline", False)
        if len(rec.get("status_history", [])) > 1:
            e["status_history"] = rec["status_history"]
        keep.append(e)

    keep.sort(key=lambda e: (e.get("date") or "9999", e["venue"], e["title"]))
    doc = {
        "updated": stamp,
        "note": ("first_seen is when this scraper first saw the show. Shows marked baseline "
                 "were already listed when the scraper first read that venue, so they are "
                 "not news."),
        "sources": sources,
        "watch": watch,
        "events": keep,
    }
    save("events.json", doc)

    window = (NOW - timedelta(days=NEW_WINDOW_DAYS)).isoformat(timespec="seconds")
    new = [e for e in keep if not e["baseline"] and e["first_seen"] >= window]
    new.sort(key=lambda e: e["first_seen"], reverse=True)
    save("new.json", {"updated": stamp, "window_days": NEW_WINDOW_DAYS, "count": len(new),
                      "events": new})
    with open(os.path.join(DATA, "new.xml"), "w", encoding="utf-8") as f:
        f.write(rss(new))

    # soldout.json: every upcoming show that reads as sold out, newest sell-out first.
    sold = [e for e in keep if e.get("sold_out_seen")]
    sold.sort(key=lambda e: e["sold_out_seen"], reverse=True)
    save("soldout.json", {
        "updated": stamp,
        "note": ("sold_out_seen is the first scraper run (every 3 hours) that saw the show "
                 "sold out. sold_out_baseline true means it was already sold out when first "
                 "seen, so the real date is earlier."),
        "count": len(sold),
        "events": sold,
    })

    # forget ids not seen for 120 days so the file stays small
    old = (NOW - timedelta(days=120)).isoformat(timespec="seconds")
    seen = {k: v for k, v in seen.items() if k in ids or v["first_seen"] >= old}
    save("seen.json", seen)

    failed = [n for n, s in sources.items() if not s["ok"]]
    print(f"{len(keep)} shows, {len(new)} new, failed: {failed or 'none'}", file=sys.stderr)


if __name__ == "__main__":
    main()
