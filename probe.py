"""Temporary probe (venue-probe branch only): fetch candidate venue calendars from
GitHub Actions and save the raw pages plus a summary, so parsers can be written
offline. Not merged to main."""
import json, os, re, sys, time
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
import scrape

OUT = os.path.join(scrape.DATA, "probe")
os.makedirs(OUT, exist_ok=True)
S = scrape.session

TARGETS = [
 ("crystal", ["https://www.crystalballroompdx.com/", "https://www.crystalballroompdx.com/events/", "https://www.mcmenamins.com/premises.aspx?id=590", "sk:1228-mcmenamins-crystal-ballroom"]),
 ("roseland", ["https://roselandpdx.com/events/", "https://roselandpdx.com/", "sk:32177-roseland-theater"]),
 ("hawthorne", ["https://hawthornetheatre.com/events/", "https://hawthornetheatre.com/"]),
 ("dantes", ["https://danteslive.com/", "https://danteslive.com/events/", "sk:35260-dantes"]),
 ("albertarose", ["https://albertarosetheatre.com/", "https://albertarosetheatre.com/events/", "sk:1000291-alberta-rose-theatre"]),
 ("turnturnturn", ["https://turnturnturnpdx.com/entertainment/", "https://turnturnturnpdx.com/", "sk:2648083-turn-turn-turn"]),
 ("the1905", ["https://www.the1905.com/", "https://the1905.com/calendar", "https://jazzoregon.org/events/venue/9328"]),
 ("kenton", ["sk:669161-world-famous-kenton-club", "sk:4638726-kenton-club", "https://www.kentonclub.com/"]),
 ("realm", ["https://realmpdx.com/", "https://www.realmpdx.com/events"]),
 ("lollipop", ["https://lollipopshoppe.com/", "https://www.lollipopshoppepdx.com/"]),
 ("swandive", ["sk:4489637-swan-dive", "https://outxout.com/venue/swandiveportland", "https://www.swandivepdx.com/"]),
 ("laurelthirst", ["https://www.laurelthirst.com/", "sk:32649-laurelthirst-public-house"]),
 ("whiteeagle", ["https://mcmenamins.com/white-eagle-saloon-hotel/white-eagle-saloon/music-event-calendar", "https://appv2.mcmenamins.com/Events.aspx?LID=347", "sk:6620-mcmenamins-white-eagle-saloon-and-hotel"]),
 ("mcmenamins", ["https://www.mcmenamins.com/to-do/live-music-events/our-venues", "https://appv2.mcmenamins.com/events.aspx"]),
 ("mission", ["https://www.mcmenamins.com/mission-theater/events"]),
 ("bossanova", ["https://bossanovaballroom.com/events"]),
 ("albertastreetpub", ["https://www.albertastreetpub.com/", "https://albertastreetpub.com/events"]),
 ("showdown", ["https://www.theshowdownpdx.com/", "https://showdownpdx.com/"]),
 ("blackwater", ["https://www.blackwaterbar.com/", "https://blackwaterbar.com/events"]),
 ("fixinto", ["https://www.thefixinto.com/"]),
 ("twilight", ["https://www.twilightcafeandbar.com/"]),
 ("oldchurch", ["https://www.theoldchurch.org/", "https://www.theoldchurch.org/events"]),
 ("landmark", ["https://www.landmarksaloon.com/"]),
 ("nofun", ["https://www.nofunbar.com/"]),
]

MARKERS = {
 "etix": "etix.com", "tixr": "tixr.com", "dice": "dice.fm", "eventbrite": "eventbrite.",
 "ticketweb": "ticketweb.com", "tw_section": "tw-section", "seetickets": "seetickets",
 "axs": "axs.com", "ticketmaster": "ticketmaster.com", "venuepilot": "venuepilot",
 "prekindle": "prekindle", "wlcr": "wlcr", "tribe": "tribe-events", "squarespace": "squarespace",
 "wix": "wixstatic", "wordpress": "wp-content", "songkick": "songkick.com", "bandsintown": "bandsintown",
 "showclix": "showclix", "ticketleap": "ticketleap", "humanitix": "humanitix", "tockify": "tockify",
 "googlecal": "calendar.google.com", "ra": "ra.co", "holdmyticket": "holdmyticket", "etix_widget": "etix-",
}

def jsonld_events(text, url):
    try:
        return scrape.jsonld_from_text(text, url)
    except Exception:
        return []

def fetch(url):
    if url.startswith("sk:"):
        url = f"https://www.songkick.com/venues/{url[3:]}/calendar"
    rec = {"url": url}
    try:
        r = S.get(url, timeout=30, allow_redirects=True)
        rec.update(status=r.status_code, final=r.url, ctype=r.headers.get("content-type", ""), length=len(r.text))
        text = r.text
    except Exception as exc:
        rec.update(error=f"{type(exc).__name__}: {exc}"[:200])
        return rec, None
    low = text.lower()
    rec["markers"] = {k: low.count(v) for k, v in MARKERS.items() if v in low}
    soup = BeautifulSoup(text, "html.parser")
    rec["title"] = (soup.title.get_text(" ").strip()[:120] if soup.title else "")
    ev = []
    for s in soup.find_all("script", type="application/ld+json"):
        raw = s.string or s.get_text() or ""
        for m in re.finditer(r'"@type"\s*:\s*"([A-Za-z]*Event)"', raw):
            ev.append(m.group(1))
    rec["jsonld_event_types"] = len(ev)
    names = re.findall(r'"name"\s*:\s*"([^"]{2,80})"', " ".join((s.string or "") for s in soup.find_all("script", type="application/ld+json")))
    rec["jsonld_names_sample"] = names[:8]
    return rec, text

def main():
    summary = {}
    hosts = set()
    for slug, urls in TARGETS:
        summary[slug] = []
        for i, u in enumerate(urls):
            rec, text = fetch(u)
            summary[slug].append(rec)
            if text is not None:
                fn = f"{slug}_{i}.html"
                with open(os.path.join(OUT, fn), "w", encoding="utf-8") as f:
                    f.write(text[:900000])
                rec["saved"] = fn
                if "squarespace" in rec.get("markers", {}):
                    sep = "&" if "?" in rec["final"] else "?"
                    r2, t2 = fetch(rec["final"] + sep + "format=json")
                    if t2 is not None:
                        fn2 = f"{slug}_{i}_sqs.json"
                        open(os.path.join(OUT, fn2), "w", encoding="utf-8").write(t2[:900000])
                        r2["saved"] = fn2
                    summary[slug].append(r2)
            if not u.startswith("sk:"):
                p = urlparse(rec.get("final") or u)
                hosts.add(f"{p.scheme}://{p.netloc}")
            time.sleep(0.4)
    api = {}
    for h in sorted(hosts):
        for path in ["/wp-json/tribe/events/v1/events?per_page=5", "/wp-json/"]:
            try:
                r = S.get(h + path, timeout=20)
                api[h + path] = {"status": r.status_code, "len": len(r.text), "head": r.text[:300]}
                if r.status_code == 200 and "tribe" in path:
                    fn = re.sub(r"[^a-z0-9]+", "_", h.lower()) + "_tribe.json"
                    open(os.path.join(OUT, fn), "w", encoding="utf-8").write(r.text[:900000])
            except Exception as exc:
                api[h + path] = {"error": str(exc)[:150]}
            time.sleep(0.3)
    summary["_api"] = api
    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1)[:60000])

if __name__ == "__main__":
    main()
