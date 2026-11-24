# marquee-feeds

Every three hours this repo reads public show calendars for a set of Portland, Oregon music venues and writes them into one list.

| File | What it is |
|---|---|
| `data/events.json` | Every upcoming show it can see, with the date it was first seen |
| `data/new.json` | Shows first seen in the last 14 days, newest first |
| `data/new.xml` | The same new shows as an RSS feed |
| `data/seen.json` | When each show was first seen, so a show that briefly drops off a page isn't reported again |

Venues: Mississippi Studios, Polaris Hall, Revolution Hall, Show Bar, Aladdin Theater, Star Theater, Jack London Revue, The Get Down, Wonder Ballroom, Holocene, Pioneer Courthouse Square and McMenamins Edgefield; since November 2026 also the Crystal Ballroom, Roseland Theater, Hawthorne Theatre (and its Lounge), Alberta Rose Theatre, Dante's, Turn! Turn! Turn!, McMenamins White Eagle Saloon and Mission Theater, The Showdown, Laurelthirst Public House, Alberta Street Pub and Swan Dive. Promoters on some of these sites also list their shows at other rooms (Keller Auditorium, Arlene Schnitzer Concert Hall), so a few of those appear too. It also checks whether the Doug Fir Lounge website is back up.

Laurelthirst's site only shows the next few days, so its shows appear a few days ahead rather than weeks.

Not covered, and why (checked November 2026): Kenton Club (no calendar on its site), REALM / 45 East (sells through Eventim; no calendar page of its own), Bossanova Ballroom (its site's security certificate fails), The 1905 (no calendar of its own; Jazz Oregon's listing timed out), The Fixin' To and The Old Church (Wix sites that only render in a browser), Black Water Bar, Twilight Cafe and No Fun (no site answered at the addresses tried), Lollipop Shoppe (closed). Songkick venue pages refuse the scraper (HTTP 406), so they can't stand in for these.

Each venue is read from its own feed or calendar page. If one venue fails, its previous list is kept and the failure shows up under `sources` in `events.json`.

Raw data: `https://raw.githubusercontent.com/goldwave315/marquee-feeds/main/data/new.json`
