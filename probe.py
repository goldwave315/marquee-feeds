"""Temporary probe (venue-probe branch only): fetch candidate venue calendars from
GitHub Actions and save script-stripped, redacted pages so parsers can be written
offline. Not merged to main."""
import json, os, re, time
import requests
from bs4 import BeautifulSoup
import scrape

OUT = os.path.join(scrape.DATA, "probe")
os.makedirs(OUT, exist_ok=True)
S = scrape.session

PAGES = {
 "opendate_turn": "https://app.opendate.io/v/turn-turn-turn-3116?layout=iframe",
 "opendate_turn2": "https://app.opendate.io/v/turn-turn-turn-3116",
 "mcm_whiteeagle": "https://www.mcmenamins.com/white-eagle-saloon-hotel",
 "mcm_whiteeagle_venue": "https://www.mcmenamins.com/white-eagle-saloon-hotel/white-eagle-saloon",
 "mcm_mission": "https://www.mcmenamins.com/mission-theater",
 "mcm_kennedy": "https://www.mcmenamins.com/kennedy-school",
 "mcm_alsden": "https://www.mcmenamins.com/crystal-hotel/als-den",
 "kenton_home": "https://www.kentonclub.com/",
 "swandive_home": "https://www.swandivepdx.com/",
}
SQS_OLD = {
 "kenton": ["https://www.kentonclub.com/", "https://www.kentonclub.com/events", "https://www.kentonclub.com/calendar", "https://www.kentonclub.com/shows"],
 "swandive": ["https://www.swandivepdx.com/", "https://www.swandivepdx.com/events", "https://www.swandivepdx.com/calendar", "https://www.swandivepdx.com/shows"],
 "asp": ["https://www.albertastreetpub.com/", "https://www.albertastreetpub.com/calendar", "https://www.albertastreetpub.com/music", "https://www.albertastreetpub.com/events-1"],
}
TRIBE_OLD = {"crystal_tribe": "https://www.crystalballroompdx.com/wp-json/tribe/events/v1/events?per_page=10"}

SECRET = re.compile(r"(?:sk|pk|tk)\.eyJ[\w.\-]+|AIza[0-9A-Za-z\-_]{35}|(?:sk|pk|rk)_(?:live|test)_\w{8,}|"
                    r"eyJ[\w\-]{10,}\.[\w\-]{10,}\.[\w\-]{5,}|AKIA[0-9A-Z]{16}|xox[baprs]-[\w-]+|gh[pousr]_\w{30,}")

def redact(t):
    return SECRET.sub("[REDACTED]", t)

def strip_html(text):
    soup = BeautifulSoup(text, "html.parser")
    for s in soup.find_all(["script", "style", "noscript", "svg"]):
        if s.name == "script" and (s.get("type") or "") == "application/ld+json":
            continue
        s.decompose()
    return redact(str(soup))

def save(name, text):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        f.write(text[:700000])

def main():
    log = {}
    for k, u in PAGES.items():
        try:
            r = S.get(u, timeout=60, allow_redirects=True)
            body = strip_html(r.text)
            save(f"{k}.html", body)
            log[k] = {"status": r.status_code, "final": r.url, "len": len(r.text), "saved_len": len(body)}
        except Exception as exc:
            log[k] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        time.sleep(0.4)
    for k, urls in {}.items():
        out = []
        for u in urls:
            try:
                r = S.get(u + "?format=json", timeout=30)
                rec = {"url": u, "status": r.status_code}
                try:
                    j = r.json()
                except ValueError:
                    rec["note"] = "not json"; out.append(rec); continue
                coll = j.get("collection") or {}
                rec["collection"] = {x: coll.get(x) for x in ("urlId", "typeName", "type", "title") if x in coll}
                nav = []
                for key in ("websiteNavigation",):
                    pass
                for listkey in ("upcoming", "items", "past"):
                    items = j.get(listkey)
                    if isinstance(items, list):
                        rec[listkey] = [{f: it.get(f) for f in ("title", "startDate", "endDate", "fullUrl", "excerpt", "id", "location") if f in it}
                                        for it in items[:40]]
                        for it in rec[listkey]:
                            if isinstance(it.get("excerpt"), str):
                                it["excerpt"] = re.sub(r"<[^>]+>", " ", it["excerpt"])[:200]
                            if isinstance(it.get("location"), dict):
                                it["location"] = {z: it["location"].get(z) for z in ("addressTitle",)}
                rec["keys"] = sorted(j.keys())[:40]
                # collections on the site, from the home page JSON
                ws = j.get("website") or {}
                rec["site_title"] = ws.get("siteTitle")
                out.append(rec)
            except Exception as exc:
                out.append({"url": u, "error": str(exc)[:150]})
            time.sleep(0.4)
        save(f"{k}_sqs.json", redact(json.dumps(out, indent=1)))
        log[k] = [(o.get("url"), o.get("status"), o.get("collection"), len(o.get("upcoming") or o.get("items") or [])) for o in out]
    for k, u in {}.items():
        try:
            r = S.get(u, timeout=30)
            j = r.json()
            slim = {"total": j.get("total"), "total_pages": j.get("total_pages"),
                    "events": [{f: e.get(f) for f in ("id", "title", "start_date", "url", "cost", "categories", "venue")} for e in j.get("events", [])]}
            for e in slim["events"]:
                if isinstance(e.get("venue"), dict):
                    e["venue"] = e["venue"].get("venue")
                if isinstance(e.get("categories"), list):
                    e["categories"] = [c.get("name") for c in e["categories"] if isinstance(c, dict)]
            save(f"{k}.json", redact(json.dumps(slim, indent=1)))
            log[k] = {"status": r.status_code, "total": j.get("total")}
        except Exception as exc:
            log[k] = {"error": str(exc)[:200]}
    save("log.json", json.dumps(log, indent=1))
    print(json.dumps(log, indent=1))

if __name__ == "__main__":
    main()
